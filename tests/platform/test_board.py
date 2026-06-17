"""Tests for board detection (Story 5.5).

``/proc/device-tree/model`` is mocked by repointing ``board._MODEL_PATH`` at a
temp file — no Raspberry Pi needed. (The root conftest extends the stdlib
``platform`` module into a package so ``platform.board`` is importable.)
"""

from __future__ import annotations

import logging

import platform.board as board_mod
from platform.board import Board, detect_board, reset_board_cache


def _set_model(monkeypatch, tmp_path, content: bytes | None) -> None:
    if content is None:
        monkeypatch.setattr(board_mod, "_MODEL_PATH", tmp_path / "does_not_exist")
    else:
        p = tmp_path / "model"
        p.write_bytes(content)
        monkeypatch.setattr(board_mod, "_MODEL_PATH", p)
    reset_board_cache()


class TestDetection:
    def test_rpi4(self, monkeypatch, tmp_path) -> None:
        _set_model(monkeypatch, tmp_path, b"Raspberry Pi 4 Model B Rev 1.4\x00")
        assert detect_board(reload=True) is Board.RPI4

    def test_rpi5(self, monkeypatch, tmp_path) -> None:
        _set_model(monkeypatch, tmp_path, b"Raspberry Pi 5 Model B\x00")
        assert detect_board(reload=True) is Board.RPI5

    def test_case_insensitive_substring(self, monkeypatch, tmp_path) -> None:
        _set_model(monkeypatch, tmp_path, b"raspberry pi 5\x00")
        assert detect_board(reload=True) is Board.RPI5

    def test_handles_trailing_nul(self, monkeypatch, tmp_path) -> None:
        # The NUL terminator must not prevent matching.
        _set_model(monkeypatch, tmp_path, b"Raspberry Pi 4 Model B\x00\x00")
        assert detect_board(reload=True) is Board.RPI4


class TestFallback:
    def test_missing_file_is_unknown(self, monkeypatch, tmp_path, caplog) -> None:
        _set_model(monkeypatch, tmp_path, None)
        with caplog.at_level(logging.WARNING, logger="platform.board"):
            assert detect_board(reload=True) is Board.UNKNOWN
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_unrecognised_model_is_unknown(self, monkeypatch, tmp_path) -> None:
        _set_model(monkeypatch, tmp_path, b"Generic x86_64 PC\x00")
        assert detect_board(reload=True) is Board.UNKNOWN

    def test_detection_never_raises(self, monkeypatch, tmp_path) -> None:
        # Even a read error degrades to UNKNOWN rather than crashing.
        class _BoomPath:
            def exists(self):
                return True

            def read_bytes(self):
                raise OSError("permission denied")

        monkeypatch.setattr(board_mod, "_MODEL_PATH", _BoomPath())
        reset_board_cache()
        assert detect_board(reload=True) is Board.UNKNOWN


class TestCaching:
    def test_result_is_cached(self, monkeypatch, tmp_path) -> None:
        _set_model(monkeypatch, tmp_path, b"Raspberry Pi 4 Model B\x00")
        assert detect_board(reload=True) is Board.RPI4
        # Repoint to RPi5 but WITHOUT reload → cached RPi4 is returned.
        p = tmp_path / "model5"
        p.write_bytes(b"Raspberry Pi 5\x00")
        monkeypatch.setattr(board_mod, "_MODEL_PATH", p)
        assert detect_board() is Board.RPI4          # cached
        assert detect_board(reload=True) is Board.RPI5  # forced re-read
