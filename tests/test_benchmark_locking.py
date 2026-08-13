"""Small process-level regression checks for the locking benchmark."""

import pytest

from benchmarks.taskplan_locking import run_benchmark


def test_process_benchmark_verifies_discovery_sqlite_and_lockmaster():
    result = run_benchmark(workers=2, tasks_per_worker=2, projects=5)

    assert result["pass"] is True
    assert result["discovery"]["discovered_projects"] == 5
    assert result["sqlite_taskplan"]["stored_tasks"] == 4
    assert result["lockmaster"]["claims_succeeded"] == 1
    assert result["scope"]["physical_machines_tested"] is False


def test_process_benchmark_rejects_non_positive_parameters():
    with pytest.raises(ValueError, match="must be > 0"):
        run_benchmark(workers=0)
