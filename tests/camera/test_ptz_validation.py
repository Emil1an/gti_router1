"""Tests for PTZCommandValidator — anti-replay + rate-limit (Story 4.4).

Pure/synchronous validation; clock is real but we build timestamps relative to
``now`` so there is no dependency on the wall clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from camera.validator import (
    REASON_EXPIRED,
    REASON_FOREIGN_CAMERA,
    REASON_RATE_LIMITED,
    REASON_STALE,
    REASON_UNKNOWN_TYPE,
    PTZCommandValidator,
)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _now_iso() -> str:
    return _iso(datetime.now(tz=UTC))


def _ago_iso(seconds: float) -> str:
    return _iso(datetime.now(tz=UTC) - timedelta(seconds=seconds))


def _ahead_iso(seconds: float) -> str:
    return _iso(datetime.now(tz=UTC) + timedelta(seconds=seconds))


def _cmd(command_type="ptz_continuous_move", camera_id="cam-1",
         issued_at=None, expires_at=None) -> dict:
    c = {
        "id": "x", "camera_id": camera_id, "command_type": command_type,
        "issued_at": issued_at if issued_at is not None else _now_iso(),
    }
    if expires_at is not None:
        c["expires_at"] = expires_at
    return c


def _validator(rate_limit_per_min=60, max_age_s=30, camera_ids=("cam-1", "cam-2")):
    return PTZCommandValidator(
        camera_ids=list(camera_ids), max_age_s=max_age_s,
        rate_limit_per_min=rate_limit_per_min,
    )


# ── Happy path ───────────────────────────────────────────────────────────────────

class TestValid:
    def test_fresh_move_ok(self) -> None:
        assert _validator().validate(_cmd()).ok is True

    def test_fresh_within_window_ok(self) -> None:
        assert _validator().validate(_cmd(issued_at=_ago_iso(10))).ok is True


# ── Anti-replay (freshness / expiry) ─────────────────────────────────────────────

class TestAntiReplay:
    def test_stale_rejected(self) -> None:
        res = _validator().validate(_cmd(issued_at=_ago_iso(31)))
        assert res.ok is False
        assert res.reason == REASON_STALE

    def test_just_under_threshold_ok(self) -> None:
        assert _validator(max_age_s=30).validate(_cmd(issued_at=_ago_iso(29))).ok is True

    def test_expired_rejected(self) -> None:
        res = _validator().validate(_cmd(expires_at=_ago_iso(1)))
        assert res.ok is False
        assert res.reason == REASON_EXPIRED

    def test_future_expiry_ok(self) -> None:
        assert _validator().validate(_cmd(expires_at=_ahead_iso(60))).ok is True

    def test_future_issued_not_stale(self) -> None:
        # Clock skew: a slightly-future issued_at must not be flagged stale.
        assert _validator().validate(_cmd(issued_at=_ahead_iso(5))).ok is True


# ── Camera membership ────────────────────────────────────────────────────────────

class TestMembership:
    def test_foreign_camera_rejected(self) -> None:
        res = _validator().validate(_cmd(camera_id="cam-foreign"))
        assert res.ok is False
        assert res.reason == REASON_FOREIGN_CAMERA


# ── Unknown command type ─────────────────────────────────────────────────────────

class TestUnknownType:
    def test_unknown_type_rejected(self) -> None:
        res = _validator().validate(_cmd(command_type="ptz_dance"))
        assert res.ok is False
        assert res.reason == REASON_UNKNOWN_TYPE


# ── Rate-limit ───────────────────────────────────────────────────────────────────

class TestRateLimit:
    def test_over_limit_rejected(self) -> None:
        v = _validator(rate_limit_per_min=2)
        assert v.validate(_cmd()).ok is True   # 1
        assert v.validate(_cmd()).ok is True   # 2
        res = v.validate(_cmd())               # 3 → over
        assert res.ok is False
        assert res.reason == REASON_RATE_LIMITED

    def test_ptz_stop_never_rate_limited(self) -> None:
        v = _validator(rate_limit_per_min=1)
        assert v.validate(_cmd()).ok is True                       # uses the 1 slot
        assert v.validate(_cmd()).ok is False                      # move now limited
        # ptz_stop must ALWAYS pass — even far over the limit.
        for _ in range(10):
            assert v.validate(_cmd(command_type="ptz_stop")).ok is True

    def test_get_position_never_rate_limited(self) -> None:
        v = _validator(rate_limit_per_min=1)
        assert v.validate(_cmd()).ok is True
        assert v.validate(_cmd()).ok is False
        for _ in range(10):
            assert v.validate(_cmd(command_type="ptz_get_position")).ok is True

    def test_exempt_commands_do_not_consume_budget(self) -> None:
        v = _validator(rate_limit_per_min=2)
        # Stops / position queries between moves must not eat the move budget.
        v.validate(_cmd(command_type="ptz_stop"))
        v.validate(_cmd(command_type="ptz_get_position"))
        assert v.validate(_cmd()).ok is True   # 1
        assert v.validate(_cmd()).ok is True   # 2
        assert v.validate(_cmd()).ok is False  # 3 → over


# ── Exempt commands still validated for freshness + membership ──────────────────

class TestExemptStillValidated:
    def test_stop_still_subject_to_freshness(self) -> None:
        res = _validator().validate(_cmd(command_type="ptz_stop", issued_at=_ago_iso(31)))
        assert res.ok is False
        assert res.reason == REASON_STALE

    def test_get_position_subject_to_membership(self) -> None:
        res = _validator().validate(
            _cmd(command_type="ptz_get_position", camera_id="cam-foreign")
        )
        assert res.ok is False
        assert res.reason == REASON_FOREIGN_CAMERA


# ── Rejection counter ────────────────────────────────────────────────────────────

class TestRejectionCounter:
    def test_rejected_count_increments(self) -> None:
        v = _validator()
        v.validate(_cmd(issued_at=_ago_iso(31)))     # stale
        v.validate(_cmd(camera_id="cam-x"))          # foreign
        v.validate(_cmd(command_type="ptz_dance"))   # unknown
        assert v.rejected_count == 3
