#!/usr/bin/env python3
"""FlyCam read-only flight telemetry and mission dispatcher service."""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import logging
import os
import re
import sqlite3
import ssl
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

LOGGER = logging.getLogger("flycam.dispatcher")
MAX_REQUEST_BYTES = 1024 * 1024
MISSION_STATUSES = {"planned", "assigned", "in_progress", "completed", "cancelled"}
ROLE_PERMISSIONS = {
    "viewer": frozenset({"read"}),
    "ingest": frozenset({"ingest"}),
    "operator": frozenset({"read", "mission"}),
    "admin": frozenset({"read", "ingest", "mission", "admin"}),
}
DETECTION_CLASS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_DETECTIONS_PER_REQUEST = 100


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
        self._detection_insert_count = 0
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
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

                CREATE TABLE IF NOT EXISTS detections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    received_at TEXT NOT NULL,
                    vehicle_id INTEGER NOT NULL,
                    object_class TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source TEXT,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS detections_vehicle_time
                    ON detections(vehicle_id, received_at DESC);
                CREATE INDEX IF NOT EXISTS detections_class_time
                    ON detections(object_class, received_at DESC);

                CREATE TABLE IF NOT EXISTS security_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    remote_address TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    role TEXT,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS security_audit_time
                    ON security_audit(occurred_at DESC);
                """
            )

    def store_telemetry(self, payload: dict[str, Any]) -> None:
        vehicle_id = int(payload["vehicleId"])
        received_at = utc_now()
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connection() as connection:
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
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT vehicle_id, received_at, payload FROM telemetry_latest ORDER BY vehicle_id"
            ).fetchall()
        return [
            {**json.loads(row["payload"]), "receivedAt": row["received_at"]}
            for row in rows
        ]

    def telemetry_history(self, vehicle_id: int, limit: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
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
        with self._connection() as connection:
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
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
        if row is None:
            raise KeyError(mission_id)
        return _mission_row(row)

    def list_missions(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
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
        with self._connection() as connection:
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
        with self._connection() as connection:
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
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, received_at, payload FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {"id": row["id"], "receivedAt": row["received_at"], **json.loads(row["payload"])}
            for row in rows
        ]

    def store_detections(self, detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        received_at = utc_now()
        stored: list[dict[str, Any]] = []
        telemetry_cache: dict[int, tuple[dict[str, Any], str] | None] = {}
        with self._connection() as connection:
            for detection in detections:
                enriched_detection = dict(detection)
                vehicle_id = int(enriched_detection["vehicleId"])
                if vehicle_id not in telemetry_cache:
                    telemetry_row = connection.execute(
                        "SELECT received_at, payload FROM telemetry_latest WHERE vehicle_id = ?",
                        (vehicle_id,),
                    ).fetchone()
                    telemetry_cache[vehicle_id] = (
                        (json.loads(telemetry_row["payload"]), telemetry_row["received_at"])
                        if telemetry_row
                        else None
                    )
                latest_telemetry = telemetry_cache[vehicle_id]
                if latest_telemetry:
                    telemetry_payload, telemetry_received_at = latest_telemetry
                    for field in ("latitude", "longitude", "altitude"):
                        if field not in enriched_detection and field in telemetry_payload:
                            enriched_detection[field] = telemetry_payload[field]
                    enriched_detection.setdefault("telemetryReceivedAt", telemetry_received_at)

                payload_json = json.dumps(
                    enriched_detection, ensure_ascii=False, separators=(",", ":")
                )
                cursor = connection.execute(
                    """
                    INSERT INTO detections(
                        received_at, vehicle_id, object_class, confidence, source, payload
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        received_at,
                        enriched_detection["vehicleId"],
                        enriched_detection["objectClass"],
                        enriched_detection["confidence"],
                        enriched_detection.get("source"),
                        payload_json,
                    ),
                )
                stored.append(
                    {"id": int(cursor.lastrowid), "receivedAt": received_at, **enriched_detection}
                )
                self._detection_insert_count += 1
            if self._detection_insert_count >= 1000:
                self._apply_retention(connection)
                self._detection_insert_count = 0
        return stored

    def list_detections(
        self,
        limit: int,
        vehicle_id: int | None = None,
        object_class: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        values: list[Any] = []
        if vehicle_id is not None:
            filters.append("vehicle_id = ?")
            values.append(vehicle_id)
        if object_class:
            filters.append("object_class = ?")
            values.append(object_class)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        values.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT id, received_at, payload FROM detections "
                f"{where_clause} ORDER BY id DESC LIMIT ?",
                values,
            ).fetchall()
        return [
            {"id": row["id"], "receivedAt": row["received_at"], **json.loads(row["payload"])}
            for row in rows
        ]

    def store_security_audit(
        self,
        *,
        remote_address: str,
        method: str,
        path: str,
        action: str,
        outcome: str,
        role: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO security_audit(
                    occurred_at, remote_address, method, path, action, outcome, role, detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now(),
                    remote_address[:100],
                    method[:16],
                    path[:500],
                    action[:100],
                    outcome[:32],
                    role[:32] if role else None,
                    detail[:500] if detail else None,
                ),
            )

    def list_security_audit(self, limit: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM security_audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "occurredAt": row["occurred_at"],
                "remoteAddress": row["remote_address"],
                "method": row["method"],
                "path": row["path"],
                "action": row["action"],
                "outcome": row["outcome"],
                "role": row["role"],
                "detail": row["detail"],
            }
            for row in rows
        ]

    def _apply_retention(self, connection: sqlite3.Connection) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        connection.execute("DELETE FROM telemetry_history WHERE received_at < ?", (cutoff,))
        connection.execute("DELETE FROM events WHERE received_at < ?", (cutoff,))
        connection.execute("DELETE FROM detections WHERE received_at < ?", (cutoff,))
        connection.execute("DELETE FROM security_audit WHERE occurred_at < ?", (cutoff,))


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


def _validate_detection(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("each detection must be a JSON object")
    if "vehicleId" not in payload:
        raise ValueError("vehicleId is required")
    payload["vehicleId"] = _validate_vehicle_id(payload["vehicleId"])

    object_class = str(payload.get("objectClass", "")).strip().lower()
    if not DETECTION_CLASS_PATTERN.fullmatch(object_class):
        raise ValueError("objectClass must use 1-64 lowercase letters, digits, '_' or '-'")
    payload["objectClass"] = object_class

    try:
        confidence = float(payload.get("confidence"))
    except (TypeError, ValueError) as error:
        raise ValueError("confidence must be numeric") from error
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    payload["confidence"] = confidence

    bbox = payload.get("bbox")
    if bbox is not None:
        if not isinstance(bbox, dict):
            raise ValueError("bbox must be an object")
        normalized_bbox: dict[str, float] = {}
        for field in ("x", "y", "width", "height"):
            try:
                value = float(bbox[field])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"bbox.{field} must be numeric") from error
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"bbox.{field} must be between 0 and 1")
            normalized_bbox[field] = value
        if normalized_bbox["x"] + normalized_bbox["width"] > 1.001:
            raise ValueError("bbox exceeds frame width")
        if normalized_bbox["y"] + normalized_bbox["height"] > 1.001:
            raise ValueError("bbox exceeds frame height")
        payload["bbox"] = normalized_bbox

    for field, minimum, maximum in (
        ("latitude", -90.0, 90.0),
        ("longitude", -180.0, 180.0),
    ):
        if field in payload and payload[field] is not None:
            try:
                value = float(payload[field])
            except (TypeError, ValueError) as error:
                raise ValueError(f"{field} must be numeric") from error
            if not minimum <= value <= maximum:
                raise ValueError(f"{field} is outside the allowed range")
            payload[field] = value

    if "source" in payload:
        payload["source"] = str(payload["source"])[:200]
    if "trackId" in payload:
        payload["trackId"] = str(payload["trackId"])[:100]
    payload["timestampUtc"] = str(payload.get("timestampUtc") or utc_now())[:64]
    return payload


def _validate_detection_batch(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and "detections" in payload:
        raw_detections = payload["detections"]
    else:
        raw_detections = [payload]
    if not isinstance(raw_detections, list) or not raw_detections:
        raise ValueError("detections must be a non-empty array")
    if len(raw_detections) > MAX_DETECTIONS_PER_REQUEST:
        raise ValueError(f"no more than {MAX_DETECTIONS_PER_REQUEST} detections per request")
    return [_validate_detection(detection) for detection in raw_detections]


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
    api_keys: tuple[tuple[str, str], ...]
    web_root: Path
    tls_enabled: bool
    client_certificate_required: bool
    authenticated_role: str | None = None
    server_version = "FlyCamDispatcher/1.1"

    def log_message(self, message_format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), message_format % args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_file(self.web_root / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "timeUtc": utc_now(),
                    "tls": self.tls_enabled,
                    "clientCertificateRequired": self.client_certificate_required,
                },
            )
            return
        permission = "admin" if parsed.path == "/api/v1/security/audit" else "read"
        if not self._authorized(permission):
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
        elif parsed.path == "/api/v1/detections":
            limit = _bounded_limit(query, default=100, maximum=5000)
            try:
                vehicle_id_value = query.get("vehicleId", [""])[0]
                vehicle_id = _validate_vehicle_id(vehicle_id_value) if vehicle_id_value else None
                object_class = query.get("class", [""])[0].strip().lower() or None
                if object_class and not DETECTION_CLASS_PATTERN.fullmatch(object_class):
                    raise ValueError("invalid class filter")
            except ValueError as error:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "detections": self.database.list_detections(
                        limit, vehicle_id=vehicle_id, object_class=object_class
                    )
                },
            )
        elif parsed.path == "/api/v1/security/audit":
            limit = _bounded_limit(query, default=100, maximum=1000)
            self._send_json(
                HTTPStatus.OK, {"audit": self.database.list_security_audit(limit)}
            )
        else:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        permission = "mission" if parsed.path == "/api/v1/missions" else "ingest"
        if not self._authorized(permission):
            return
        try:
            payload = self._read_json()
            if parsed.path == "/api/v1/telemetry":
                telemetry = _validate_telemetry(payload)
                self.database.store_telemetry(telemetry)
                self._send_json(HTTPStatus.ACCEPTED, {"accepted": True})
            elif parsed.path == "/api/v1/missions":
                mission = self.database.create_mission(_validate_mission(payload))
                self._audit("mission.create", "allowed", detail=f"missionId={mission['id']}")
                self._send_json(HTTPStatus.CREATED, mission)
            elif parsed.path == "/api/v1/events":
                event = self.database.store_event(_validate_event(payload))
                self._send_json(HTTPStatus.CREATED, event)
            elif parsed.path == "/api/v1/detections":
                detections = self.database.store_detections(_validate_detection_batch(payload))
                self._send_json(
                    HTTPStatus.CREATED,
                    {
                        "accepted": len(detections),
                        "detectionIds": [detection["id"] for detection in detections],
                    },
                )
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
        except ValueError as error:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._authorized("mission"):
            return
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 4 or parts[:3] != ["api", "v1", "missions"]:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            mission_id = int(parts[3])
            payload = _validate_mission(self._read_json(), partial=True)
            mission = self.database.update_mission(mission_id, payload)
            self._audit("mission.update", "allowed", detail=f"missionId={mission_id}")
            self._send_json(HTTPStatus.OK, mission)
        except ValueError as error:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except KeyError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "mission not found")

    def _authorized(self, required_permission: str) -> bool:
        self.authenticated_role = None
        if not self.api_keys:
            return True
        supplied_key = self.headers.get("X-API-Key", "")
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            supplied_key = authorization[7:]

        matched_role: str | None = None
        supplied_bytes = supplied_key.encode("utf-8")
        for role, configured_key in self.api_keys:
            if hmac.compare_digest(supplied_bytes, configured_key.encode("utf-8")):
                matched_role = role
        if matched_role is None:
            self._audit("authenticate", "denied", detail="invalid-or-missing-key")
            self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid or missing API key")
            return False

        self.authenticated_role = matched_role
        if required_permission in ROLE_PERMISSIONS[matched_role]:
            return True
        self._audit(
            "authorize",
            "denied",
            detail=f"permission={required_permission}",
        )
        self._send_error_json(HTTPStatus.FORBIDDEN, "API key does not grant this operation")
        return False

    def _audit(self, action: str, outcome: str, detail: str | None = None) -> None:
        try:
            self.database.store_security_audit(
                remote_address=str(self.client_address[0]),
                method=self.command,
                path=urlparse(self.path).path,
                action=action,
                outcome=outcome,
                role=self.authenticated_role,
                detail=detail,
            )
        except (OSError, sqlite3.Error):
            LOGGER.exception("Unable to write security audit record")

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
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self.tls_enabled:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
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


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_loopback_host(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_server(
    host: str,
    port: int,
    database_path: Path,
    api_key: str = "",
    retention_days: int = 30,
    *,
    admin_key: str = "",
    ingest_key: str = "",
    viewer_key: str = "",
    operator_key: str = "",
    tls_cert: Path | None = None,
    tls_key: Path | None = None,
    tls_ca: Path | None = None,
    require_client_certificate: bool = False,
    allow_insecure_network: bool = False,
) -> ThreadingHTTPServer:
    api_keys = tuple(
        (role, key)
        for role, key in (
            ("viewer", viewer_key),
            ("ingest", ingest_key),
            ("operator", operator_key),
            ("admin", admin_key),
            ("admin", api_key),
        )
        if key
    )
    key_values = [key for _, key in api_keys]
    if len(key_values) != len(set(key_values)):
        raise ValueError("each API role must use a different key")
    if tls_key and not tls_cert:
        raise ValueError("--tls-key requires --tls-cert")
    if (tls_ca or require_client_certificate) and not tls_cert:
        raise ValueError("client certificate options require --tls-cert")
    if require_client_certificate and not tls_ca:
        raise ValueError("--require-client-cert requires --tls-ca")
    if (
        not _is_loopback_host(host)
        and not api_keys
        and not require_client_certificate
        and not allow_insecure_network
    ):
        raise ValueError(
            "refusing unauthenticated non-loopback listener; configure an API key or mTLS"
        )

    database = Database(database_path, retention_days)
    web_root = Path(__file__).resolve().parent / "web"

    class ConfiguredHandler(DispatcherRequestHandler):
        pass

    ConfiguredHandler.database = database
    ConfiguredHandler.api_keys = api_keys
    ConfiguredHandler.web_root = web_root
    ConfiguredHandler.tls_enabled = bool(tls_cert)
    ConfiguredHandler.client_certificate_required = require_client_certificate
    server = ThreadingHTTPServer((host, port), ConfiguredHandler)
    server.daemon_threads = True

    if tls_cert:
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(
                certfile=str(tls_cert), keyfile=str(tls_key) if tls_key else None
            )
            if tls_ca:
                context.load_verify_locations(cafile=str(tls_ca))
                context.verify_mode = (
                    ssl.CERT_REQUIRED if require_client_certificate else ssl.CERT_OPTIONAL
                )
            server.socket = context.wrap_socket(server.socket, server_side=True)
        except Exception:
            server.server_close()
            raise
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
    parser.add_argument("--admin-key", default=os.getenv("FLYCAM_ADMIN_KEY", ""))
    parser.add_argument("--ingest-key", default=os.getenv("FLYCAM_INGEST_KEY", ""))
    parser.add_argument("--viewer-key", default=os.getenv("FLYCAM_VIEWER_KEY", ""))
    parser.add_argument("--operator-key", default=os.getenv("FLYCAM_OPERATOR_KEY", ""))
    parser.add_argument("--tls-cert", type=Path, default=os.getenv("FLYCAM_TLS_CERT") or None)
    parser.add_argument("--tls-key", type=Path, default=os.getenv("FLYCAM_TLS_KEY") or None)
    parser.add_argument("--tls-ca", type=Path, default=os.getenv("FLYCAM_TLS_CA") or None)
    parser.add_argument(
        "--require-client-cert",
        action="store_true",
        default=_env_flag("FLYCAM_REQUIRE_CLIENT_CERT"),
    )
    parser.add_argument(
        "--allow-insecure-network",
        action="store_true",
        default=_env_flag("FLYCAM_ALLOW_INSECURE_NETWORK"),
        help="allow a non-loopback listener without API keys (unsafe; isolated test networks only)",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=int(os.getenv("FLYCAM_RETENTION_DAYS", "30")),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(args.db)
    try:
        server = create_server(
            args.host,
            args.port,
            args.db,
            args.api_key,
            args.retention_days,
            admin_key=args.admin_key,
            ingest_key=args.ingest_key,
            viewer_key=args.viewer_key,
            operator_key=args.operator_key,
            tls_cert=args.tls_cert,
            tls_key=args.tls_key,
            tls_ca=args.tls_ca,
            require_client_certificate=args.require_client_cert,
            allow_insecure_network=args.allow_insecure_network,
        )
    except (OSError, ssl.SSLError, ValueError) as error:
        raise SystemExit(f"Dispatcher configuration error: {error}") from error
    scheme = "https" if args.tls_cert else "http"
    LOGGER.info("FlyCam dispatcher listening on %s://%s:%s", scheme, args.host, server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping dispatcher")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
