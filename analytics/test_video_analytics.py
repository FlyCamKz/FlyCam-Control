from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .video_analytics import (
    Detection,
    DispatcherPublisher,
    is_live_source,
    normalized_box,
    parse_args,
    parse_video_source,
    sanitized_source_label,
)


class _CaptureHandler(BaseHTTPRequestHandler):
    payload: dict | None = None
    api_key = ""

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers["Content-Length"])
        type(self).payload = json.loads(self.rfile.read(content_length))
        type(self).api_key = self.headers.get("X-API-Key", "")
        body = json.dumps({"accepted": len(type(self).payload["detections"])}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


class VideoAnalyticsTest(unittest.TestCase):
    def test_source_label_removes_credentials_and_query(self) -> None:
        label = sanitized_source_label("rtsp://operator:secret@10.0.0.5:8554/live?token=hidden")
        self.assertEqual(label, "rtsp://10.0.0.5:8554/live")
        self.assertNotIn("secret", label)
        self.assertNotIn("token", label)

    def test_numeric_source_is_camera_index(self) -> None:
        self.assertEqual(parse_video_source("0"), 0)
        self.assertEqual(sanitized_source_label(0), "camera-0")
        self.assertTrue(is_live_source(0))
        self.assertTrue(is_live_source("rtsp://camera/live"))
        self.assertFalse(is_live_source("recording.mp4"))

    def test_normalized_box_clips_letterboxed_coordinates(self) -> None:
        box = normalized_box(
            320, 320, 320, 160,
            frame_width=1280,
            frame_height=720,
            scale=0.5,
            pad_x=0,
            pad_y=140,
        )
        self.assertEqual(box, {"x": 0.25, "y": 0.277778, "width": 0.5, "height": 0.444444})

    def test_detection_payload_has_model_traceability(self) -> None:
        payload = Detection("person", 0.91234567, {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}).to_payload(
            vehicle_id=7,
            source="camera-0",
            model_name="people-cars.onnx",
            model_hash="a" * 64,
            frame_width=1920,
            frame_height=1080,
        )
        self.assertEqual(payload["vehicleId"], 7)
        self.assertEqual(payload["objectClass"], "person")
        self.assertEqual(payload["confidence"], 0.912346)
        self.assertEqual(payload["modelHashSha256"], "a" * 64)

    def test_publisher_posts_detection_batch_and_key(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            publisher = DispatcherPublisher(
                f"http://127.0.0.1:{server.server_port}", "ingest-secret"
            )
            accepted = publisher.publish([{"vehicleId": 1, "objectClass": "car"}])
            self.assertEqual(accepted, 1)
            self.assertEqual(_CaptureHandler.api_key, "ingest-secret")
            self.assertEqual(_CaptureHandler.payload["detections"][0]["objectClass"], "car")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_self_test_does_not_require_operational_arguments(self) -> None:
        args = parse_args(["--self-test"])
        self.assertTrue(args.self_test)


if __name__ == "__main__":
    unittest.main()
