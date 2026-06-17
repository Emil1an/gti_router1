"""Tests for the no-detection cross-system contract (Story 6.4)."""

from __future__ import annotations

from pathlib import Path

from utils.contract import (
    CONTRACT_VERSION,
    GATEWAY_SOURCE,
    ROUTER_SOURCE,
    no_detection_contract,
)

# Detection/ML frameworks that must NEVER appear in Router source (AR8: the
# Router never runs inference — all detection is the Gateway's job).
_FORBIDDEN_DETECTION_IMPORTS = (
    "torch", "tensorflow", "ultralytics", "onnxruntime",
    "tflite_runtime", "yolov", "mmdet", "detectron2", "mediapipe",
)

_SRC = Path(__file__).resolve().parent.parent / "src"


class TestContract:
    def test_router_source_value(self) -> None:
        assert ROUTER_SOURCE == "router"
        assert GATEWAY_SOURCE == "gateway"

    def test_no_detection_contract_marks_router_and_version(self) -> None:
        c = no_detection_contract()
        assert c["source"] == "router"
        assert c["contract_version"] == CONTRACT_VERSION

    def test_contract_has_no_detection_fields(self) -> None:
        # The marking must NOT carry any detection semantics (Story 6.4 AC#3).
        c = no_detection_contract()
        keys = {k.lower() for k in c}
        assert not any(
            tok in k for k in keys for tok in ("detect", "label", "bbox", "class", "score")
        )

    def test_contract_values_are_strings(self) -> None:
        # Must be attachable directly as S3 object metadata.
        assert all(isinstance(v, str) for v in no_detection_contract().values())


class TestNoModelsGuard:
    def test_router_imports_no_detection_frameworks(self) -> None:
        """Static guard: no detection/ML framework is imported anywhere in src/."""
        offenders: list[str] = []
        for py in _SRC.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                stripped = line.strip()
                if not (stripped.startswith("import ") or stripped.startswith("from ")):
                    continue
                for forbidden in _FORBIDDEN_DETECTION_IMPORTS:
                    if forbidden in stripped:
                        offenders.append(f"{py.name}: {stripped}")
        assert offenders == [], f"Router must not import detection frameworks: {offenders}"
