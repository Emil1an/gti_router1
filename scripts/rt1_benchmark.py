#!/usr/bin/env python3
"""RT1 gate — capture-card encoding benchmark (Story 5.2, AC#6).

RT1 is the architecture's single **critical** risk: is software H.264 encoding of
a capture card feasible on an RPi5 within the per-stream CPU budget (NFR1,
<70%/stream)?  This script measures CPU / temperature / encode latency while
encoding from a V4L2 device with the board-appropriate encoder, and prints a
GO / NO-GO verdict.

It is a **manual hardware checklist** to be run on real RPi4 and RPi5 boards
before the pilot — it is NOT part of CI (CI only validates the selection logic in
``tests/camera/test_encoder.py``).

Usage (on the Raspberry Pi)::

    sudo apt install ffmpeg            # FFmpeg 5.1 (Bookworm)
    python3 scripts/rt1_benchmark.py --device /dev/video0 --duration 60

Record the printed results (board, encoder, mean/max CPU %, max temp °C, and the
GO/NO-GO) in the architecture handoff. NO-GO ⇒ reduce camera count or revisit the
contingency (static FFmpeg 7.1 build) — never enable HEVC-SW (forbidden) and
never degrade per-stream quality (quality over quantity).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from camera.encoder import EncoderSelector  # noqa: E402
from platform.board import detect_board  # noqa: E402

# Per-stream CPU budget (NFR1). Mean CPU above this for a single stream = NO-GO.
_CPU_BUDGET_PERCENT = 70.0


def _read_temp_celsius() -> float | None:
    try:
        import psutil

        temps = psutil.sensors_temperatures()
        for key in ("cpu_thermal", "coretemp", "cpu-thermal"):
            if temps.get(key):
                return float(temps[key][0].current)
    except Exception:
        pass
    zone = Path("/sys/class/thermal/thermal_zone0/temp")
    if zone.exists():
        try:
            return int(zone.read_text().strip()) / 1000.0
        except (OSError, ValueError):
            return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="RT1 capture-card encoding benchmark")
    parser.add_argument("--device", default="/dev/video0", help="V4L2 device")
    parser.add_argument("--duration", type=int, default=60, help="seconds to encode")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()

    try:
        import psutil
    except ImportError:
        print("psutil is required: pip install psutil", file=sys.stderr)
        return 2

    board = detect_board()
    encoder = EncoderSelector(board).select()
    print(f"Board: {board.value}  Encoder: {encoder.encoder} (hardware={encoder.hardware})")

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "v4l2", "-framerate", str(args.fps),
        "-video_size", f"{args.width}x{args.height}", "-i", args.device,
        *encoder.to_ffmpeg_args(),
        "-t", str(args.duration),
        "-f", "null", "-",
    ]
    print("Running:", " ".join(cmd))

    start = time.monotonic()
    proc = subprocess.Popen(cmd)
    samples: list[float] = []
    temps: list[float] = []
    psutil.cpu_percent(interval=None)  # prime
    try:
        while proc.poll() is None:
            time.sleep(1.0)
            samples.append(psutil.cpu_percent(interval=None))
            t = _read_temp_celsius()
            if t is not None:
                temps.append(t)
    finally:
        if proc.poll() is None:
            proc.terminate()
    elapsed = time.monotonic() - start

    if not samples:
        print("No samples collected — did FFmpeg start?", file=sys.stderr)
        return 2

    mean_cpu = sum(samples) / len(samples)
    max_cpu = max(samples)
    max_temp = max(temps) if temps else float("nan")

    print("\n── RT1 results ─────────────────────────────────")
    print(f"  duration_s        : {elapsed:.1f}")
    print(f"  cpu_percent (mean): {mean_cpu:.1f}")
    print(f"  cpu_percent (max) : {max_cpu:.1f}")
    print(f"  temperature_max_c : {max_temp:.1f}")
    verdict = "GO" if mean_cpu <= _CPU_BUDGET_PERCENT else "NO-GO"
    print(f"  budget_percent    : {_CPU_BUDGET_PERCENT}")
    print(f"  VERDICT           : {verdict}")
    print("────────────────────────────────────────────────")
    print("Record these values in the architecture handoff. NO-GO ⇒ reduce camera "
          "count (never degrade per-stream quality, never enable HEVC-SW).")
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
