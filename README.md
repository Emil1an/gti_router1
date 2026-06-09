# GTI Router

Edge node that captures IP-camera video via RTSP, segments it into HLS, and uploads continuously to AWS S3.  
Designed for 24/7 unattended operation on Raspberry Pi 4 (Base SKU) and Raspberry Pi 5 (Pro SKU).

## Hardware requirements

| SKU | Board | RAM | Cameras | GPS | Capture card |
|-----|-------|-----|---------|-----|--------------|
| **Base** | Raspberry Pi 4 2 GB | 2 GB | 1 IP (RTSP) | No | No |
| **Pro** | Raspberry Pi 5 | 4/8 GB | Up to 3 IP + 1 capture card | Yes | Yes |

OS: **Raspberry Pi OS Lite 64-bit (Bookworm)** — Python 3.11 is the system default.

## Setup (development)

### Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python package manager
- Python 3.11+
- `ffmpeg` ≥ 5.1 from system packages (`sudo apt install ffmpeg`)

### Install

```bash
git clone https://github.com/gti/gti-router.git
cd gti-router
uv sync
```

### Configuration

```bash
cp config/router.yaml.example config/router.yaml
# Edit config/router.yaml with your camera URL and AWS/Supabase credentials
export ROUTER_CONFIG=config/router.yaml
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export SUPABASE_SERVICE_ROLE_KEY=...
```

### Run

```bash
uv run python src/main.py
```

### Tests

```bash
uv run pytest tests/ -v
```

### Lint / format

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## Zero-terminal deployment (RPi)

1. Flash Raspberry Pi OS Lite 64-bit to the SD card.
2. Copy `config/router.yaml.example` to `/boot/router.yaml` (boot partition, FAT32 — editable on any OS) and fill in your values.
3. Create `/etc/gti-router/env` with the secret env vars:
   ```
   AWS_ACCESS_KEY_ID=...
   AWS_SECRET_ACCESS_KEY=...
   SUPABASE_SERVICE_ROLE_KEY=...
   ```
4. Install and enable the systemd service (Story 1.6):
   ```bash
   sudo bash scripts/install.sh
   ```
5. On first boot the service copies `/boot/router.yaml` → `/etc/gti-router/router.yaml` (mode 0600).

## Project structure

```
src/
├── main.py           — async orchestrator (no business logic)
├── config/           — YAML loading and pydantic validation (get_config())
├── utils/            — logging, retry, typed errors
├── camera/sources/   — RTSPSource, CaptureCardSource
├── pipeline/         — FFmpeg HLS pipeline, buffer
├── upload/           — S3 client, upload queue
├── storage/          — SQLite state store
├── health/           — registration, reporter, monitor, watchdog
├── location/         — GPS, orientation, last-frame snapshot
└── platform/         — board detection (RPi4/RPi5)
tests/
├── fixtures/sample.mp4   — 10 s H.264 test video
└── ...                   — mirrors src/ structure
config/router.yaml.example
```

## Log levels

| Level | When to use |
|-------|-------------|
| `DEBUG` | Low-level tracing (FFmpeg stderr, retry countdown, packet counts) |
| `INFO` | Normal lifecycle events (connected, segment uploaded, health reported) |
| `WARNING` | Degraded operation not yet requiring intervention (retry, high temp) |
| `ERROR` | Failures requiring attention or triggering fallback (upload exhausted, cam unavailable) |

Logs target **systemd-journald**; view with `journalctl -u gti-router -f`.
