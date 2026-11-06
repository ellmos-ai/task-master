"""Pure return accounting for leased tasks: is a task flapping, must it stop?

The consumer (BACH, Ocean) reads its own lease history and passes the release
events oldest first; nothing here touches a store, a lease or a provider.

A reason with the prefix ``host_precondition:`` means the worker's
environment failed (lock scanner, twin, canonical repository). The task itself
is fine, so such returns flag ``flapping`` but never block the task: blocking
every queued task for a broken host would hide the one thing to repair.
"""
from datetime import datetime, timedelta

CONTRACT_VERSION = "task-returns.v1"
HOST_PREFIX = "host_precondition:"


def is_host_scoped(reason):
    return isinstance(reason, str) and reason.startswith(HOST_PREFIX)


def _aware(value, name):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def summarize_returns(events, *, now, window_seconds=600, flapping_threshold=5):
    """Counters since the last progress event.

    events: mappings with ``at`` (aware datetime or ISO string), ``reason``
    (str) and ``progress`` (True for a release that delivered a result).
    """
    now = _aware(now, "now")
    returns = []
    for event in events:
        at = _aware(event["at"], "event at")
        if event.get("progress"):
            returns = []
        else:
            returns.append((at, event.get("reason") or ""))
    last_at, last_reason = returns[-1] if returns else (None, None)
    identical = 0
    for _, reason in reversed(returns):
        if reason != last_reason:
            break
        identical += 1
    since = now - timedelta(seconds=window_seconds)
    return {
        "return_count": len(returns),
        "last_return_reason": last_reason,
        "last_return_at": last_at.isoformat() if last_at else None,
        "consecutive_identical": identical,
        "flapping": sum(1 for at, _ in returns if at >= since) >= flapping_threshold,
        "host_scoped": is_host_scoped(last_reason),
    }


def block_reason(summary, *, threshold=5):
    """Fixed reason to block the task, or None while returns may still differ."""
    reason = summary["last_return_reason"]
    if not reason or summary["host_scoped"] or summary["consecutive_identical"] < threshold:
        return None
    return f"repeated_return:{reason}"
