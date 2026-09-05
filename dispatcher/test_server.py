from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import sqlite3
from pathlib import Path
from unittest.mock import patch

try:
    from .server import create_server, default_database_path
    from .crypto import DataEncryptor
except ImportError:
    from server import create_server, default_database_path
    from crypto import DataEncryptor


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
        api_key: str = "test-key",
    ) -> tuple[int, dict]:
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["X-API-Key"] = api_key
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
        self.assertFalse(payload["tls"])

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

        status, audit_payload = self.request("GET", "/api/v1/security/audit?limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(audit_payload["audit"][0]["outcome"], "denied")
        self.assertNotIn("test-key", json.dumps(audit_payload))

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

    def test_encrypted_database_round_trip_and_no_plaintext_at_rest(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        database_path = Path(self.temp_directory.name) / "encrypted.sqlite3"
        encryptor = DataEncryptor({"test-2026": bytes(range(32))}, "test-2026")
        self.server = create_server(
            "127.0.0.1",
            0,
            database_path,
            "test-key",
            data_encryptor=encryptor,
            require_data_encryption=True,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

        status, health = self.request("GET", "/health", authorized=False)
        self.assertEqual(status, 200)
        self.assertTrue(health["dataAtRestEncryption"])
        self.assertEqual(health["dataEncryptionProvider"], "aes-256-gcm")
        self.assertEqual(health["activeDataKeyId"], "test-2026")

        status, _ = self.request(
            "POST",
            "/api/v1/telemetry",
            {"vehicleId": 1, "flightMode": "SecretMission", "latitude": 43.2389},
        )
        self.assertEqual(status, 202)
        status, vehicles = self.request("GET", "/api/v1/vehicles")
        self.assertEqual(status, 200)
        self.assertEqual(vehicles["vehicles"][0]["flightMode"], "SecretMission")

        with sqlite3.connect(database_path) as connection:
            stored = connection.execute("SELECT payload FROM telemetry_latest").fetchone()[0]
        self.assertTrue(stored.startswith("flycam:v1:test-2026:"))
        self.assertNotIn("SecretMission", stored)

    def test_required_data_encryption_refuses_missing_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "no data key"):
            create_server(
                "127.0.0.1",
                0,
                Path(self.temp_directory.name) / "required.sqlite3",
                require_data_encryption=True,
            )

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

    def test_detection_batch_round_trip(self) -> None:
        status, _ = self.request(
            "POST",
            "/api/v1/telemetry",
            {"vehicleId": 2, "latitude": 43.2389, "longitude": 76.8897, "altitude": 120},
        )
        self.assertEqual(status, 202)
        status, accepted = self.request(
            "POST",
            "/api/v1/detections",
            {
                "detections": [
                    {
                        "vehicleId": 2,
                        "objectClass": "person",
                        "confidence": 0.93,
                        "source": "rtsp-camera-1",
                        "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
                    },
                    {
                        "vehicleId": 2,
                        "objectClass": "car",
                        "confidence": 0.88,
                    },
                ]
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(accepted["accepted"], 2)
        self.assertEqual(len(accepted["detectionIds"]), 2)

        status, payload = self.request(
            "GET", "/api/v1/detections?vehicleId=2&class=person&limit=10"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["detections"]), 1)
        self.assertEqual(payload["detections"][0]["objectClass"], "person")
        self.assertEqual(payload["detections"][0]["bbox"]["width"], 0.3)
        self.assertEqual(payload["detections"][0]["latitude"], 43.2389)
        self.assertIn("telemetryReceivedAt", payload["detections"][0])

    def test_detection_validation_rejects_invalid_bbox(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/v1/detections",
            {
                "vehicleId": 1,
                "objectClass": "person",
                "confidence": 0.8,
                "bbox": {"x": 0.9, "y": 0.1, "width": 0.2, "height": 0.2},
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("frame width", payload["error"])

    def test_role_scoped_keys_enforce_least_privilege(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        database_path = Path(self.temp_directory.name) / "roles.sqlite3"
        self.server = create_server(
            "127.0.0.1",
            0,
            database_path,
            admin_key="admin-secret",
            ingest_key="ingest-secret",
            viewer_key="viewer-secret",
            operator_key="operator-secret",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

        status, _ = self.request(
            "POST",
            "/api/v1/telemetry",
            {"vehicleId": 1},
            api_key="ingest-secret",
        )
        self.assertEqual(status, 202)
        status, _ = self.request("GET", "/api/v1/vehicles", api_key="ingest-secret")
        self.assertEqual(status, 403)
        status, _ = self.request("GET", "/api/v1/vehicles", api_key="viewer-secret")
        self.assertEqual(status, 200)
        status, _ = self.request(
            "POST",
            "/api/v1/missions",
            {"name": "Restricted mission"},
            api_key="viewer-secret",
        )
        self.assertEqual(status, 403)
        status, _ = self.request(
            "POST",
            "/api/v1/missions",
            {"name": "Operator mission"},
            api_key="operator-secret",
        )
        self.assertEqual(status, 201)
        status, audit = self.request(
            "GET", "/api/v1/security/audit", api_key="admin-secret"
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(audit["audit"]), 2)

    def test_duplicate_role_keys_are_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "different key"):
            create_server(
                "127.0.0.1",
                0,
                Path(self.temp_directory.name) / "duplicate.sqlite3",
                admin_key="same-secret",
                viewer_key="same-secret",
            )

    def test_unauthenticated_network_listener_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "refusing unauthenticated"):
            create_server("0.0.0.0", 0, Path(self.temp_directory.name) / "unsafe.sqlite3")

    def test_client_certificate_requires_ca(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires --tls-ca"):
            create_server(
                "127.0.0.1",
                0,
                Path(self.temp_directory.name) / "mtls.sqlite3",
                tls_cert=Path("server.pem"),
                require_client_certificate=True,
            )


if __name__ == "__main__":
    unittest.main()
