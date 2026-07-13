"""Tests for the last-frame path resolver (tmpfs-preferred, disk fallback)."""

from __future__ import annotations

from pathlib import Path

from utils import frame_store


def _write(p: Path, data: bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_prefers_ram_when_present(tmp_path: Path) -> None:
    out, ram = tmp_path / "disk", tmp_path / "shm"
    ram.mkdir()
    _write(out / "cam-1" / "last_frame.jpg", b"disk")
    _write(ram / "cam-1" / "last_frame.jpg", b"ram")

    resolved = frame_store.resolve_last_frame("cam-1", out, ram)
    assert resolved == ram / "cam-1" / "last_frame.jpg"
    assert resolved.read_bytes() == b"ram"  # RAM wins


def test_falls_back_to_disk(tmp_path: Path) -> None:
    out, ram = tmp_path / "disk", tmp_path / "shm"
    ram.mkdir()  # tmpfs dir exists but has no frame for this camera
    _write(out / "cam-1" / "last_frame.jpg", b"disk")

    resolved = frame_store.resolve_last_frame("cam-1", out, ram)
    assert resolved == out / "cam-1" / "last_frame.jpg"


def test_none_when_no_frame_anywhere(tmp_path: Path) -> None:
    out, ram = tmp_path / "disk", tmp_path / "nope" / "shm"  # ram parent missing
    out.mkdir()
    assert frame_store.resolve_last_frame("cam-1", out, ram) is None


def test_scratch_dir_prefers_ram(tmp_path: Path) -> None:
    out, ram = tmp_path / "disk", tmp_path / "shm"
    ram.mkdir()
    d = frame_store.scratch_dir("cam-1", out, ram)
    assert d == ram / "cam-1" and d.is_dir()


def test_scratch_dir_falls_back_to_disk(tmp_path: Path) -> None:
    out, ram = tmp_path / "disk", tmp_path / "nope" / "shm"  # ram parent missing
    d = frame_store.scratch_dir("cam-1", out, ram)
    assert d == out / "cam-1" and d.is_dir()
