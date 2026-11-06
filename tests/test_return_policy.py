"""Return accounting for leased tasks (PB-1, BACH #3985).

Measured 06.11.2026: two workers returned 159 tasks ~557 times an hour for
days; #1340 reached fence 396, #3736 fence 4835. Nothing counted the returns,
so nothing stopped them and the GUI could not show them.
"""
from datetime import datetime, timedelta, timezone

import pytest

from taskplan.return_policy import block_reason, is_host_scoped, summarize_returns

T0 = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)


def ret(minutes, reason="task_scope_unavailable:missing_branch"):
    return {"at": T0 + timedelta(minutes=minutes), "reason": reason, "progress": False}


def progress(minutes):
    return {"at": T0 + timedelta(minutes=minutes), "reason": "", "progress": True}


def test_no_events_is_a_quiet_task():
    summary = summarize_returns([], now=T0)
    assert summary == {"return_count": 0, "last_return_reason": None, "last_return_at": None,
                       "consecutive_identical": 0, "flapping": False, "host_scoped": False}


def test_counts_returns_and_trailing_identical_reason():
    events = [ret(0, "a"), ret(1, "b"), ret(2, "b"), ret(3, "b")]
    summary = summarize_returns(events, now=T0 + timedelta(minutes=4))
    assert summary["return_count"] == 4
    assert summary["consecutive_identical"] == 3
    assert summary["last_return_reason"] == "b"
    assert summary["last_return_at"] == "2026-11-06T18:03:00+00:00"


def test_progress_resets_the_count():
    events = [ret(0), ret(1), progress(2), ret(3)]
    summary = summarize_returns(events, now=T0 + timedelta(minutes=4))
    assert summary["return_count"] == 1
    assert summary["consecutive_identical"] == 1


def test_flapping_needs_the_returns_inside_the_window():
    burst = [ret(m) for m in range(5)]
    assert summarize_returns(burst, now=T0 + timedelta(minutes=5))["flapping"] is True
    spread = [ret(m * 15) for m in range(5)]
    assert summarize_returns(spread, now=T0 + timedelta(minutes=61))["flapping"] is False


def test_block_after_threshold_identical_task_scoped_returns():
    events = [ret(m) for m in range(5)]
    summary = summarize_returns(events, now=T0 + timedelta(minutes=5))
    assert block_reason(summary, threshold=5) == "repeated_return:task_scope_unavailable:missing_branch"
    four = summarize_returns(events[:4], now=T0 + timedelta(minutes=5))
    assert block_reason(four, threshold=5) is None


def test_host_scoped_returns_never_block_the_task():
    events = [ret(m, "host_precondition:twin_status_unknown") for m in range(20)]
    summary = summarize_returns(events, now=T0 + timedelta(minutes=20))
    assert summary["host_scoped"] is True
    assert summary["flapping"] is True
    assert block_reason(summary, threshold=5) is None
    assert is_host_scoped("host_precondition:twin_status_unknown")
    assert not is_host_scoped("task_scope_unavailable:host_precondition")


def test_naive_timestamps_are_refused():
    with pytest.raises(ValueError):
        summarize_returns([{"at": datetime(2026, 11, 6), "reason": "x", "progress": False}], now=T0)
    with pytest.raises(ValueError):
        summarize_returns([], now=datetime(2026, 11, 6))
