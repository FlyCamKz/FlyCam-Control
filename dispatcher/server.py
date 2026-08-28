#!/usr/bin/env python3
"""FlyCam read-only flight telemetry and mission dispatcher service."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

LOGGER = logging.getLogger("flycam.dispatcher")
MAX_REQUEST_BYTES = 1024 * 1024
MISSION_STATUSES = {"planned", "assigned", "in_progress", "completed", "cancelled"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def default_database_path() -> Path:
    configured_path = os.getenv("FLYCAM_DB")
    if configured_path:
        return Path(configured_path)

    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "FlyCam" / "Dispatcher" / "flycam-dispatcher.sqlite3"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FlyCam" / "Dispatcher" / "flycam-dispatcher.sqlite3"

    state_root = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_root / "flycam-dispatcher" / "flycam-dispatcher.sqlite3"


def configure_logging(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(database_path.parent / "flycam-dispatcher.log", encoding="utf-8")
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


class Database:
    def __init__(self, path: Path, retention_days: int = 30) -> None:
        self.path = path
        self.retention_days = max(1, retention_days)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._telemetry_insert_count = 0
        self._event_insert_count = 0
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS telemetry_latest (
                    vehicle_id INTEGER PRIMARY KEY,
                    received_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telemetry_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vehicle_id INTEGER NOT NULL,
                    received_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS telemetry_history_vehicle_time
                    ON telemetry_history(vehicle_id, received_at DESC);

                CREATE TABLE IF NOT EXISTS missions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    vehicle_id INTEGER,
                    origin TEXT,
                    destination TEXT,
                    scheduled_at TEXT,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    received_at TEXT NOT NULL,
                    vehicle_id INTEGER,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_time ON events(received_at DESC);
                """
            )

    def store_telemetry(self, payload: dict[str, Any]) -> None:
        vehicle_id = int(payload["vehicleId"])
        received_at = utc_now()
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO telemetry_latest(vehicle_id, received_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(vehicle_id) DO UPDATE SET
                    received_at = excluded.received_at,
                    payload = excluded.payload
                """,
                (vehicle_id, received_at, payload_json),
            )
            connection.execute(
                "INSERT INTO telemetry_history(vehicle_id, received_at, payload) VALUES (?, ?, ?)",
                (vehicle_id, received_at, payload_json),
            )

            self._telemetry_insert_count += 1
            if self._telemetry_insert_count % 1000 == 0:
                self._apply_retention(connection)

    def list_vehicles(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT vehicle_id, received_at, payload FROM telemetry_latest ORDER BY vehicle_id"
            ).fetchall()
        return [
            {**json.loads(row["payload"]), "receivedAt": row["received_at"]}
            for row in rows
        ]

    def telemetry_history(self, vehicle_id: int, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT received_at, payload
                FROM telemetry_history
                WHERE vehicle_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (vehicle_id, limit),
            ).fetchall()
        return [
            {**json.loads(row["payload"]), "receivedAt": row["received_at"]}
            for row in rows
        ]

    def create_mission(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        status = str(payload.get("status", "planned"))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO missions(
                    created_at, updated_at, name, status, vehicle_id,
                    origin, destination, scheduled_at, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    now,
                    payload["name"],
                    status,
                    payload.get("vehicleId"),
                    payload.get("origin"),
                    payload.get("destination"),
                    payload.get("scheduledAt"),
                    payload.get("notes"),
                ),
            )
            mission_id = int(cursor.lastrowid)
        return self.get_mission(mission_id)

    def get_mission(self, mission_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
        if row is None:
            raise KeyError(mission_id)
        return _mission_row(row)

    def list_missions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM missions ORDER BY id DESC").fetchall()
        return [_mission_row(row) for row in rows]

    def update_mission(self, mission_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        allowed_fields = {
            "name": "name",
            "status": "status",
            "vehicleId": "vehicle_id",
            "origin": "origin",
            "destination": "destination",
            "scheduledAt": "scheduled_at",
            "notes": "notes",
        }
        updates: list[str] = []
        values: list[Any] = []
        for api_name, column_name in allowed_fields.items():
            if api_name in payload:
                updates.append(f"{column_name} = ?")
                values.append(payload[api_name])
        if not updates:
            return self.get_mission(mission_id)

        updates.append("updated_at = ?")
        values.append(utc_now())
        values.append(mission_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE missions SET {', '.join(updates)} WHERE id = ?", values
            )
            if cursor.rowcount == 0:
                raise KeyError(mission_id)
        return self.get_mission(mission_id)

    def store_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        received_at = utc_now()
        event_type = str(payload.get("eventType", "operator-event"))
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO events(received_at, vehicle_id, event_type, payload) VALUES (?, ?, ?, ?)",
                (received_at, payload.get("vehicleId"), event_type, payload_json),
            )
            event_id = int(cursor.lastrowid)
            self._event_insert_count += 1
            if self._event_insert_count % 1000 == 0:
                self._apply_retention(connection)
        return {"id": event_id, "receivedAt": received_at, **payload}

    def list_events(self, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, received_at, payload FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {"id": row["id"], "receivedAt": row["received_at"], **json.loads(row["payload"])}
            for row in rows
        ]

    def _apply_retention(self, connection: sqlite3.Connection) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        connection.execute("DELETE FROM telemetry_history WHERE received_at < ?", (cutoff,))
        connection.execute("DELETE FROM events WHERE received_at < ?", (cutoff,))


def _mission_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "name": row["name"],
        "status": row["status"],
        "vehicleId": row["vehicle_id"],
        "origin": row["origin"],
        "destination": row["destination"],
        "scheduledAt": row["scheduled_at"],
        "notes": row["notes"],
    }


def _validate_telemetry(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("JSON object expected")
    if "vehicleId" not in payload:
        raise ValueError("vehicleId is required")
    payload["vehicleId"] = _validate_vehicle_id(payload["vehicleId"])

    for field, minimum, maximum in (
        ("latitude", -90.0, 90.0),
        ("longitude", -180.0, 180.0),
        ("mavlinkLossPercent", 0.0, 100.0),
    ):
        if field in payload:
            try:
                value = float(payload[field])
            except (TypeError, ValueError) as error:
                raise ValueError(f"{field} must be numeric") from error
            if not minimum <= value <= maximum:
                raise ValueError(f"{field} is outside the allowed range")
            payload[field] = value

    payload.setdefault("timestampUtc", utc_now())
    return payload


def _validate_mission(payload: Any, partial: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("JSON object expected")
    if not partial and not str(payload.get("name", "")).strip():
        raise ValueError("name is required")
    if "name" in payload:
        payload["name"] = str(payload["name"]).strip()[:200]
        if not payload["name"]:
            raise ValueError("name cannot be empty")
    if "status" in payload:
        payload["status"] = str(payload["status"])
        if payload["status"] not in MISSION_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(MISSION_STATUSES))}")
    if "vehicleId" in payload and payload["vehicleId"] is not None:
        payload["vehicleId"] = _validate_vehicle_id(payload["vehicleId"])
    for field in ("origin", "destination", "scheduledAt", "notes"):
        if field in payload and payload[field] is not None:
            payload[field] = str(payload[field])[:2000]
    return payload


def _validate_event(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("JSON object expected")
    if "vehicleId" in payload and payload["vehicleId"] is not None:
        payload["vehicleId"] = _validate_vehicle_id(payload["vehicleId"])
    if "eventType" in payload:
        payload["eventType"] = str(payload["eventType"])[:100]
    if "event" in payload:
        payload["event"] = str(payload["event"])[:200]
    return payload


def _validate_vehicle_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("vehicleId must be an integer")
    try:
        vehicle_id = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("vehicleId must be an integer") from error
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("vehicleId must be an integer")
    if not 1 <= vehicle_id <= 255:
        raise ValueError("vehicleId must be between 1 and 255")
    return vehicle_id


class DispatcherRequestHandler(BaseHTTPRequestHandler):
    database: Database
    api_key: str
    web_root: Path
    server_version = "FlyCamDispatcher/1.0"

    def log_message(self, message_format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), message_format % args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_file(self.web_root / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok", "timeUtc": utc_now()})
            return
        if not self._authorized():
            return

        query = parse_qs(parsed.query)
        if parsed.path == "/api/v1/vehicles":
            self._send_json(HTTPStatus.OK, {"vehicles": self.database.list_vehicles()})
        elif parsed.path == "/api/v1/telemetry":
            try:
                vehicle_id = int(query.get("vehicleId", [""])[0])
            except ValueError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "vehicleId query parameter is required")
                return
            limit = _bounded_limit(query, default=300, maximum=5000)
            self._send_json(
                HTTPStatus.OK,
                {"vehicleId": vehicle_id, "telemetry": self.database.telemetry_history(vehicle_id, limit)},
            )
        elif parsed.path == "/api/v1/missions":
            self._send_json(HTTPStatus.OK, {"missions": self.database.list_missions()})
        elif parsed.path == "/api/v1/events":
            limit = _bounded_limit(query, default=100, maximum=1000)
            self._send_json(HTTPStatus.OK, {"events": self.database.list_events(limit)})
        else:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._authorized():
            return
        try:
            payload = self._read_json()
            if parsed.path == "/api/v1/telemetry":
                telemetry = _validate_telemetry(payload)
                self.database.store_telemetry(telemetry)
                self._send_json(HTTPStatus.ACCEPTED, {"accepted": True})
            elif parsed.path == "/api/v1/missions":
                mission = self.database.create_mission(_validate_mission(payload))
                self._send_json(HTTPStatus.CREATED, mission)
            elif parsed.path == "/api/v1/events":
                event = self.database.store_event(_validate_event(payload))
                self._send_json(HTTPStatus.CREATED, event)
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
        except ValueError as error:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._authorized():
            return
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 4 or parts[:3] != ["api", "v1", "missions"]:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            mission_id = int(parts[3])
            payload = _validate_mission(self._read_json(), partial=True)
            mission = self.database.update_mission(mission_id, payload)
            self._send_json(HTTPStatus.OK, mission)
        except ValueError as error:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except KeyError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "mission not found")

    def _authorized(self) -> bool:
        if not self.api_key:
            return True
        supplied_key = self.headers.get("X-API-Key", "")
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            supplied_key = authorization[7:]
        if hmac.compare_digest(supplied_key.encode(), self.api_key.encode()):
            return True
        self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid or missing API key")
        return False

    def _read_json(self) -> Any:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("invalid Content-Length") from error
        if content_length <= 0:
            raise ValueError("request body is required")
        if content_length > MAX_REQUEST_BYTES:
            raise ValueError("request body is too large")
        raw_body = self.rfile.read(content_length)
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("invalid JSON") from error

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message})

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self'; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )


def _bounded_limit(query: dict[str, list[str]], default: int, maximum: int) -> int:
    try:
        return max(1, min(maximum, int(query.get("limit", [str(default)])[0])))
    except ValueError:
        return default


def create_server(
    host: str,
    port: int,
    database_path: Path,
    api_key: str = "",
    retention_days: int = 30,
) -> ThreadingHTTPServer:
    database = Database(database_path, retention_days)
    web_root = Path(__file__).resolve().parent / "web"

    class ConfiguredHandler(DispatcherRequestHandler):
        pass

    ConfiguredHandler.database = database
    ConfiguredHandler.api_key = api_key
    ConfiguredHandler.web_root = web_root
    server = ThreadingHTTPServer((host, port), ConfiguredHandler)
    server.daemon_threads = True
    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("FLYCAM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("FLYCAM_PORT", "8088")))
    parser.add_argument(
        "--db",
        type=Path,
        default=default_database_path(),
    )
    parser.add_argument("--api-key", default=os.getenv("FLYCAM_API_KEY", ""))
    parser.add_argument(
        "--retention-days",
        type=int,
        default=int(os.getenv("FLYCAM_RETENTION_DAYS", "30")),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(args.db)
    server = create_server(args.host, args.port, args.db, args.api_key, args.retention_days)
    LOGGER.info("FlyCam dispatcher listening on http://%s:%s", args.host, server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping dispatcher")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
