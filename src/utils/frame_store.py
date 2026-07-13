"""Single source of truth for where a camera's ``last_frame.jpg`` lives.

Both the snapshot writer (:mod:`pipeline.snapshot`) and the local-console reader
(:mod:`web.local_api`) must agree on the location, so the resolution logic lives
here as pure path functions (no heavy imports).

Zero-Disk-Write policy
----------------------
In production on Linux the frame is written to — and read from — tmpfs
(``/dev/shm``), so the MicroSD is never touched for snapshots. When tmpfs is not
available (non-Linux / dev), everything falls back to the on-disk output dir.
"""

from __future__ import annotations

from pathlib import Path

LAST_FRAME_NAME = "last_frame.jpg"


def ram_base(ram_dir: Path) -> Path | None:
    """Return the tmpfs base dir if it is usable, else ``None``.

    Usable means the configured ``ram_dir`` (or its parent, e.g. ``/dev/shm``)
    already exists as a directory — true on Linux/Docker, false on Windows/dev.
    """
    return ram_dir if (ram_dir.is_dir() or ram_dir.parent.is_dir()) else None


def scratch_dir(camera_id: str, output_dir: Path, ram_dir: Path) -> Path:
    """Per-camera dir to WRITE the frame into (tmpfs preferred, disk fallback).

    Creates the directory. Used by the snapshot extractor.
    """
    base = ram_base(ram_dir)
    if base is not None:
        try:
            target = base / camera_id
            target.mkdir(parents=True, exist_ok=True)
            return target
        except OSError:
            pass
    fallback = output_dir / camera_id
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def last_frame_candidates(
    camera_id: str, output_dir: Path, ram_dir: Path
) -> list[Path]:
    """Candidate frame paths to READ, most-preferred (tmpfs) first. No I/O writes."""
    candidates: list[Path] = []
    base = ram_base(ram_dir)
    if base is not None:
        candidates.append(base / camera_id / LAST_FRAME_NAME)
    candidates.append(output_dir / camera_id / LAST_FRAME_NAME)
    return candidates


def resolve_last_frame(
    camera_id: str, output_dir: Path, ram_dir: Path
) -> Path | None:
    """Return the first existing frame path (tmpfs then disk), or ``None``."""
    for path in last_frame_candidates(camera_id, output_dir, ram_dir):
        if path.is_file():
            return path
    return None
