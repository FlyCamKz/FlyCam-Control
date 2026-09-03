from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

try:
    from .server import create_server, default_database_path
except ImportError:
    from server import create_server, default_database_path


class DispatcherServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_directory.name) / "test.sqlite3"
        self.server = create_server("127.0.0.1", 0, database_path, "test-key")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_directory.cleanup()

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        authorized: bool = True,
    ) -> tuple[int, dict]:
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["X-API-Key"] = "test-key"
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_health_does_not_require_authentication(self) -> None:
        status, payload = self.request("GET", "/health", authorized=False)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_dashboard_is_served(self) -> None:
        with urllib.request.urlopen(self.base_url + "/", timeout=2) as response:
            content = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("FlyCam", content)
        self.assertIn("диспетчерский центр", content)

    def test_database_path_can_be_configured_from_environment(self) -> None:
        configured_path = Path(self.temp_directory.name) / "configured.sqlite3"
        with patch.dict(os.environ, {"FLYCAM_DB": str(configured_path)}):
            self.assertEqual(default_database_path(), configured_path)

    def test_api_requires_key(self) -> None:
        status, payload = self.request("GET", "/api/v1/vehicles", authorized=False)
        self.assertEqual(status, 401)
        self.assertIn("API key", payload["error"])

    def test_telemetry_round_trip(self) -> None:
        telemetry = {
            "vehicleId": 1,
            "latitude": 43.2389,
            "longitude": 76.8897,
            "altitude": 121.5,
            "flightMode": "Mission",
            "flying": True,
            "armed": True,
        }
        status, payload = self.request("POST", "/api/v1/telemetry", telemetry)
        self.assertEqual(status, 202)
        self.assertTrue(payload["accepted"])

        status, payload = self.request("GET", "/api/v1/vehicles")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["vehicles"]), 1)
        self.assertEqual(payload["vehicles"][0]["vehicleId"], 1)

    def test_multiple_vehicles_are_stored_independently(self) -> None:
        for vehicle_id, flight_mode in ((1, "Hold"), (2, "Mission"), (7, "Return")):
            status, payload = self.request(
                "POST",
                "/api/v1/telemetry",
                {"vehicleId": vehicle_id, "flightMode": flight_mode, "armed": False},
            )
            self.assertEqual(status, 202)
            self.assertTrue(payload["accepted"])

        status, payload = self.request("GET", "/api/v1/vehicles")
        self.assertEqual(status, 200)
        self.assertEqual([vehicle["vehicleId"] for vehicle in payload["vehicles"]], [1, 2, 7])

    def test_mission_create_and_update(self) -> None:
        status, mission = self.request(
            "POST",
            "/api/v1/missions",
            {"name": "Доставка №1", "origin": "База FlyCam", "destination": "Clinic"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(mission["status"], "planned")

        status, updated = self.request(
            "PATCH", f"/api/v1/missions/{mission['id']}", {"status": "assigned", "vehicleId": 1}
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["status"], "assigned")
        self.assertEqual(updated["vehicleId"], 1)

    def test_mission_rejects_invalid_vehicle_id(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/v1/missions",
            {"name": "Некорректное задание", "vehicleId": 0},
        )
        self.assertEqual(status, 400)
        self.assertIn("between 1 and 255", payload["error"])

    def test_event_round_trip(self) -> None:
        status, created = self.request(
            "POST",
            "/api/v1/events",
            {"eventType": "cargo-bay", "event": "open-acknowledged", "vehicleId": 1},
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["event"], "open-acknowledged")

        status, payload = self.request("GET", "/api/v1/events?limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(payload["events"][0]["eventType"], "cargo-bay")

    def test_event_rejects_invalid_vehicle_id(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/v1/events",
            {"eventType": "cargo-bay", "vehicleId": 1.5},
        )
        self.assertEqual(status, 400)
        self.assertIn("must be an integer", payload["error"])


if __name__ == "__main__":
    unittest.main()
