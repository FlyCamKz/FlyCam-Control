#!/usr/bin/env python3
"""Detect selected objects in RTSP/USB/file video and publish metadata to FlyCam."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

LOGGER = logging.getLogger("flycam.analytics")

COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic_light", "fire_hydrant", "stop_sign", "parking_meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports_ball", "kite", "baseball_bat", "baseball_glove",
    "skateboard", "surfboard", "tennis_racket", "bottle", "wine_glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot_dog", "pizza", "donut", "cake", "chair", "couch",
    "potted_plant", "bed", "dining_table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell_phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy_bear", "hair_drier",
    "toothbrush",
)
DEFAULT_TARGETS = frozenset({"person", "bicycle", "car", "motorcycle", "bus", "truck"})


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitized_source_label(source: str | int) -> str:
    """Return a credential-free label suitable for logs and the dispatcher."""
    if isinstance(source, int) or str(source).isdigit():
        return f"camera-{source}"
    value = str(source)
    parsed = urlsplit(value)
    if parsed.scheme and parsed.hostname:
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path or ""
        return f"{parsed.scheme.lower()}://{host}{port}{path}"[:200]
    return (Path(value).name or "video-file")[:200]


def parse_video_source(value: str) -> str | int:
    return int(value) if value.isdigit() else value


def is_live_source(source: str | int) -> bool:
    if isinstance(source, int):
        return True
    return urlsplit(source).scheme.lower() in {"rtsp", "rtsps", "http", "https", "udp"}


def normalized_box(
    center_x: float,
    center_y: float,
    width: float,
    height: float,
    *,
    frame_width: int,
    frame_height: int,
    scale: float,
    pad_x: float,
    pad_y: float,
) -> dict[str, float]:
    """Convert a model-space center box into clipped 0..1 frame coordinates."""
    left = (center_x - width / 2.0 - pad_x) / scale
    top = (center_y - height / 2.0 - pad_y) / scale
    right = (center_x + width / 2.0 - pad_x) / scale
    bottom = (center_y + height / 2.0 - pad_y) / scale
    left = max(0.0, min(float(frame_width), left))
    top = max(0.0, min(float(frame_height), top))
    right = max(left, min(float(frame_width), right))
    bottom = max(top, min(float(frame_height), bottom))
    return {
        "x": round(left / frame_width, 6),
        "y": round(top / frame_height, 6),
        "width": round((right - left) / frame_width, 6),
        "height": round((bottom - top) / frame_height, 6),
    }


@dataclass(frozen=True)
class Detection:
    object_class: str
    confidence: float
    bbox: dict[str, float]

    def to_payload(
        self,
        *,
        vehicle_id: int,
        source: str,
        model_name: str,
        model_hash: str,
        frame_width: int,
        frame_height: int,
    ) -> dict[str, Any]:
        return {
            "vehicleId": vehicle_id,
            "objectClass": self.object_class,
            "confidence": round(self.confidence, 6),
            "bbox": self.bbox,
            "source": source,
            "timestampUtc": utc_now(),
            "modelName": model_name[:200],
            "modelHashSha256": model_hash,
            "frameWidth": frame_width,
            "frameHeight": frame_height,
        }


class YoloOnnxDetector:
    """OpenCV DNN runner for standard non-NMS Ultralytics COCO ONNX exports."""

    def __init__(
        self,
        model_path: Path,
        *,
        confidence_threshold: float = 0.5,
        nms_threshold: float = 0.45,
        input_size: int = 640,
        targets: frozenset[str] = DEFAULT_TARGETS,
        device: str = "cpu",
    ) -> None:
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError(
                "OpenCV/NumPy are required; install analytics/requirements.txt"
            ) from error

        if not model_path.is_file():
            raise ValueError(f"ONNX model was not found: {model_path}")
        if not 0.0 < confidence_threshold <= 1.0:
            raise ValueError("confidence threshold must be between 0 and 1")
        if not 0.0 < nms_threshold <= 1.0:
            raise ValueError("NMS threshold must be between 0 and 1")
        unknown_targets = targets.difference(COCO_CLASSES)
        if unknown_targets:
            raise ValueError(f"unknown COCO target classes: {', '.join(sorted(unknown_targets))}")

        self.cv2 = cv2
        self.np = np
        self.net = cv2.dnn.readNetFromONNX(str(model_path))
        if device == "cuda":
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA_FP16)
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.input_size = input_size
        self.target_ids = tuple(index for index, name in enumerate(COCO_CLASSES) if name in targets)

    def detect(self, frame: Any) -> list[Detection]:
        cv2 = self.cv2
        np = self.np
        frame_height, frame_width = frame.shape[:2]
        scale = min(self.input_size / frame_width, self.input_size / frame_height)
        resized_width = max(1, round(frame_width * scale))
        resized_height = max(1, round(frame_height * scale))
        resized = cv2.resize(frame, (resized_width, resized_height))
        canvas = np.full((self.input_size, self.input_size, 3), 114, dtype=np.uint8)
        pad_x = (self.input_size - resized_width) // 2
        pad_y = (self.input_size - resized_height) // 2
        canvas[pad_y : pad_y + resized_height, pad_x : pad_x + resized_width] = resized
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 255.0, swapRB=True, crop=False)
        self.net.setInput(blob)
        output = np.asarray(self.net.forward())
        rows = np.squeeze(output)
        if rows.ndim != 2:
            raise RuntimeError(f"unsupported ONNX output shape: {output.shape}")
        if rows.shape[0] <= 128 and rows.shape[0] < rows.shape[1]:
            rows = rows.T

        columns = rows.shape[1]
        if columns == len(COCO_CLASSES) + 4:
            score_offset = 4
            has_objectness = False
        elif columns == len(COCO_CLASSES) + 5:
            score_offset = 5
            has_objectness = True
        else:
            raise RuntimeError(
                f"unsupported ONNX output with {columns} columns; expected 84 or 85"
            )

        candidates: list[Detection] = []
        pixel_boxes: list[list[int]] = []
        confidences: list[float] = []
        candidate_class_ids: list[int] = []
        for row in rows:
            scores = row[score_offset : score_offset + len(COCO_CLASSES)]
            target_scores = scores[list(self.target_ids)]
            target_offset = int(np.argmax(target_scores))
            class_id = self.target_ids[target_offset]
            confidence = float(target_scores[target_offset])
            if has_objectness:
                confidence *= float(row[4])
            if confidence < self.confidence_threshold:
                continue

            bbox = normalized_box(
                float(row[0]), float(row[1]), float(row[2]), float(row[3]),
                frame_width=frame_width,
                frame_height=frame_height,
                scale=scale,
                pad_x=pad_x,
                pad_y=pad_y,
            )
            pixel_box = [
                round(bbox["x"] * frame_width),
                round(bbox["y"] * frame_height),
                round(bbox["width"] * frame_width),
                round(bbox["height"] * frame_height),
            ]
            if pixel_box[2] <= 0 or pixel_box[3] <= 0:
                continue
            candidates.append(Detection(COCO_CLASSES[class_id], confidence, bbox))
            pixel_boxes.append(pixel_box)
            confidences.append(confidence)
            candidate_class_ids.append(class_id)

        if not candidates:
            return []
        selected: list[int] = []
        for class_id in sorted(set(candidate_class_ids)):
            class_indices = [
                index for index, value in enumerate(candidate_class_ids) if value == class_id
            ]
            class_boxes = [pixel_boxes[index] for index in class_indices]
            class_confidences = [confidences[index] for index in class_indices]
            kept = cv2.dnn.NMSBoxes(
                class_boxes,
                class_confidences,
                self.confidence_threshold,
                self.nms_threshold,
            )
            selected.extend(class_indices[int(index)] for index in np.asarray(kept).reshape(-1))
        selected.sort(key=lambda index: confidences[index], reverse=True)
        return [candidates[index] for index in selected]


class DispatcherPublisher:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        ca_cert: Path | None = None,
        client_cert: Path | None = None,
        client_key: Path | None = None,
        timeout: float = 5.0,
    ) -> None:
        if client_key and not client_cert:
            raise ValueError("client key requires a client certificate")
        self.url = base_url.rstrip("/") + "/api/v1/detections"
        self.api_key = api_key
        self.timeout = timeout
        self.context = ssl.create_default_context(cafile=str(ca_cert) if ca_cert else None)
        if client_cert:
            self.context.load_cert_chain(
                certfile=str(client_cert), keyfile=str(client_key) if client_key else None
            )

    def publish(self, detections: Sequence[dict[str, Any]]) -> int:
        if not detections:
            return 0
        body = json.dumps({"detections": list(detections)}, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response:
            result = json.loads(response.read())
        return int(result.get("accepted", 0))


def _open_capture(cv2: Any, source: str | int) -> Any:
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"unable to open video source {sanitized_source_label(source)}")
    return capture


def run(args: argparse.Namespace) -> None:
    source = parse_video_source(args.source)
    source_label = sanitized_source_label(args.source_label or source)
    targets = frozenset(item.strip().lower() for item in args.classes.split(",") if item.strip())
    detector = YoloOnnxDetector(
        args.model,
        confidence_threshold=args.confidence,
        nms_threshold=args.nms,
        input_size=args.input_size,
        targets=targets,
        device=args.device,
    )
    model_hash = sha256_file(args.model)
    publisher = DispatcherPublisher(
        args.dispatcher_url,
        args.api_key,
        ca_cert=args.ca_cert,
        client_cert=args.client_cert,
        client_key=args.client_key,
    )
    LOGGER.info(
        "Starting analytics source=%s vehicle=%s model_sha256=%s",
        source_label,
        args.vehicle_id,
        model_hash,
    )

    frame_number = 0
    last_publish = 0.0
    reconnects = 0
    while True:
        try:
            capture = _open_capture(detector.cv2, source)
        except RuntimeError:
            reconnects += 1
            if not is_live_source(source) or (
                args.max_reconnects and reconnects > args.max_reconnects
            ):
                raise
            LOGGER.warning("Retrying video source in %.1f seconds", args.reconnect_delay)
            time.sleep(args.reconnect_delay)
            continue

        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    LOGGER.warning("Video source ended or disconnected: %s", source_label)
                    break
                frame_number += 1
                if frame_number % args.frame_interval:
                    continue
                detections = detector.detect(frame)
                now = time.monotonic()
                if detections and now - last_publish >= args.publish_interval:
                    frame_height, frame_width = frame.shape[:2]
                    payloads = [
                        detection.to_payload(
                            vehicle_id=args.vehicle_id,
                            source=source_label,
                            model_name=args.model.name,
                            model_hash=model_hash,
                            frame_width=frame_width,
                            frame_height=frame_height,
                        )
                        for detection in detections
                    ]
                    if args.dry_run:
                        LOGGER.info(
                            "Dry run detections: %s", json.dumps(payloads, ensure_ascii=False)
                        )
                    else:
                        try:
                            accepted = publisher.publish(payloads)
                            LOGGER.info("Published %s detection(s)", accepted)
                        except (OSError, ValueError, urllib.error.URLError) as error:
                            LOGGER.error("Unable to publish detections: %s", error)
                    last_publish = now
                if args.once:
                    return
        finally:
            capture.release()

        if not is_live_source(source):
            return
        reconnects += 1
        if args.max_reconnects and reconnects > args.max_reconnects:
            raise RuntimeError("video source reconnect limit reached")
        LOGGER.info("Reconnecting video source in %.1f seconds", args.reconnect_delay)
        time.sleep(args.reconnect_delay)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.getenv("FLYCAM_VIDEO_SOURCE", ""))
    parser.add_argument("--source-label", default=os.getenv("FLYCAM_VIDEO_SOURCE_LABEL", ""))
    parser.add_argument("--model", type=Path, default=os.getenv("FLYCAM_ONNX_MODEL") or None)
    parser.add_argument("--vehicle-id", type=int, default=int(os.getenv("FLYCAM_VEHICLE_ID", "0")))
    parser.add_argument(
        "--dispatcher-url",
        default=os.getenv("FLYCAM_DISPATCHER_URL", "http://127.0.0.1:8088"),
    )
    parser.add_argument("--api-key", default=os.getenv("FLYCAM_INGEST_KEY", os.getenv("FLYCAM_API_KEY", "")))
    parser.add_argument("--ca-cert", type=Path, default=os.getenv("FLYCAM_TLS_CA") or None)
    parser.add_argument("--client-cert", type=Path, default=os.getenv("FLYCAM_CLIENT_CERT") or None)
    parser.add_argument("--client-key", type=Path, default=os.getenv("FLYCAM_CLIENT_KEY") or None)
    parser.add_argument("--classes", default=",".join(sorted(DEFAULT_TARGETS)))
    parser.add_argument("--confidence", type=float, default=0.5)
    parser.add_argument("--nms", type=float, default=0.45)
    parser.add_argument("--input-size", type=int, default=640)
    parser.add_argument("--frame-interval", type=int, default=3)
    parser.add_argument("--publish-interval", type=float, default=1.0)
    parser.add_argument("--reconnect-delay", type=float, default=2.0)
    parser.add_argument(
        "--max-reconnects",
        type=int,
        default=0,
        help="maximum live-source reconnects; 0 retries forever",
    )
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return args
    if not args.source:
        parser.error("--source is required")
    if args.model is None:
        parser.error("--model is required")
    if not 1 <= args.vehicle_id <= 255:
        parser.error("--vehicle-id must be between 1 and 255")
    if args.frame_interval < 1:
        parser.error("--frame-interval must be positive")
    if args.publish_interval < 0:
        parser.error("--publish-interval cannot be negative")
    if args.reconnect_delay < 0 or args.max_reconnects < 0:
        parser.error("reconnect settings cannot be negative")
    return args


def self_test() -> None:
    import cv2  # type: ignore[import-not-found]
    import numpy  # type: ignore[import-not-found]

    print(f"FlyCam Video Analytics OK; OpenCV={cv2.__version__}; NumPy={numpy.__version__}")


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    try:
        run(args)
    except (OSError, RuntimeError, ValueError) as error:
        LOGGER.error("Analytics stopped: %s", error)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
