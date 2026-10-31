# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Unit tests for taskplan.lease (TaskLeaseManager & CLI `task` command)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from taskplan.lease import (
    TaskLeaseConflictError,
    TaskLeaseError,
    TaskLeaseManager,
)
from taskplan.__main__ import main as cli_main


def test_take_new_and_submit(tmp_path: Path) -> None:
    db_path = tmp_path / "test_tasks.db"
    mgr = TaskLeaseManager(db_path)

    rec = mgr.take(
        create_new=True,
        holder="agent@mac-studio",
        origin_host="mac-studio",
        title="Direct commissioned task",
        description="Created via take --new",
        priority="high",
        task_kind="commissioned",
        target_output="PR + pytest",
        bauort="task-master",
    )
    assert rec.task_id >= 1
    assert rec.fence == 1
    assert rec.status == "active"
    assert rec.task_kind == "commissioned"
    assert rec.target_output == "PR + pytest"
    assert rec.bauort == "task-master"

    # Second holder cannot take while active
    with pytest.raises(TaskLeaseConflictError):
        mgr.take(rec.task_id, holder="other@laptop")

    # Renew increments fence
    renewed = mgr.renew(rec.task_id, lease_id=rec.lease_id, fence=rec.fence)
    assert renewed.fence == 2

    # Stale fence fails
    with pytest.raises(TaskLeaseConflictError):
        mgr.submit(rec.task_id, lease_id=rec.lease_id, fence=1, result="done")

    # Valid fence succeeds
    submitted = mgr.submit(
        rec.task_id,
        lease_id=rec.lease_id,
        fence=2,
        result="Implemented and verified",
    )
    assert submitted.status == "submitted"
    assert submitted.result_summary == "Implemented and verified"


def test_return_requires_reason_and_reopens_task(tmp_path: Path) -> None:
    db_path = tmp_path / "test_return.db"
    mgr = TaskLeaseManager(db_path)
    rec = mgr.take(
        create_new=True,
        holder="worker@mac-studio",
        title="Discovered maintenance task",
        task_kind="discovered",
    )
    with pytest.raises(TaskLeaseError):
        mgr.return_lease(rec.task_id, lease_id=rec.lease_id, fence=rec.fence, reason="   ")

    ret = mgr.return_lease(
        rec.task_id,
        lease_id=rec.lease_id,
        fence=rec.fence,
        reason="waiting_for_dependency",
    )
    assert ret.status == "returned"
    rows = mgr.list_tasks(task_kind="discovered")
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert rows[0]["holder"] is None


def test_reclaim_expired_leases(tmp_path: Path) -> None:
    db_path = tmp_path / "test_reclaim.db"
    mgr = TaskLeaseManager(db_path)
    t0 = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    rec = mgr.take(
        create_new=True,
        holder="worker@mac-studio",
        title="Expiring task",
        ttl_profile="S",
        now=t0,
    )
    t_expired = t0 + timedelta(minutes=20)
    reclaimed = mgr.reclaim_expired(now=t_expired)
    assert rec.task_id in reclaimed


def test_cli_task_workflow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = str(tmp_path / "cli_tasks.db")
    rc = cli_main(
        [
            "task",
            "take",
            "--new",
            "--title",
            "CLI Task",
            "--by",
            "claude@laptop",
            "--kind",
            "commissioned",
            "--bauort",
            "open-ocean",
            "--db",
            db_file,
            "--json",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert '"task_id": 1' in out

    rc_block = cli_main(
        [
            "task",
            "block",
            "1",
            "--reason",
            "external_dependency",
            "--db",
            db_file,
            "--json",
        ]
    )
    assert rc_block == 0
