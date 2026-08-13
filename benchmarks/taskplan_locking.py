#!/usr/bin/env python3
"""Process-level SQLite and LockMaster benchmark for taskplan.

The run uses separate Python processes against a shared local filesystem. That
models the contention and visibility boundary relevant to multiple workers,
but it does **not** certify SQLite or LockMaster on a network filesystem or on
physically separate machines.

Examples::

    python benchmarks/taskplan_locking.py --workers 4 --tasks-per-worker 20 --projects 200 --output results/benchmark_locking_20260813.json
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from taskplan.client import TaskClient  # noqa: E402
from taskplan.locks import LazyLockView, scan_lockmaster  # noqa: E402
from taskplan.traversal import Level, TraversalConfig, find_projects  # noqa: E402


def _git_revision() -> str | None:
    """Read the local revision without contacting a remote."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPOSITORY_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else None


def _sqlite_writer(payload: tuple[str, int, int]) -> dict:
    """Write tasks from one independent process and report every failure."""
    db_path, worker_id, task_count = payload
    client = TaskClient(db_path, agent_id=f"benchmark-machine-{worker_id}")
    successes = 0
    errors: list[str] = []
    for index in range(task_count):
        try:
            client.add(
                title=f"benchmark-{worker_id}-{index}",
                effort="easy",
                project_path=f"benchmark-project-{worker_id}",
                root_id="benchmark",
                source="process-benchmark",
            )
            successes += 1
        except Exception as exc:  # pragma: no cover - depends on OS contention
            errors.append(f"{type(exc).__name__}: {exc}")
    return {"worker": worker_id, "successes": successes, "errors": errors}


def _lock_contender(payload: tuple[str, int, int]) -> dict:
    """Race for one shared LockMaster file, then read its visible state."""
    root_path, worker_id, attempts = payload
    root = Path(root_path)
    project = root / "shared-project"
    project.mkdir(parents=True, exist_ok=True)
    lock_path = project / "LOCK.benchmark.txt"
    won = False
    errors: list[str] = []
    for _ in range(attempts):
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                handle.write(f"worker={worker_id}\n")
            won = True
            break
        except FileExistsError:
            break
        except OSError as exc:  # pragma: no cover - depends on filesystem
            errors.append(f"{type(exc).__name__}: {exc}")
            break

    view = LazyLockView([root])
    visible = bool(view.locks_for(project))
    selection_allowed = view.allows_selection(project)
    return {
        "worker": worker_id,
        "won_claim": won,
        "lock_visible": visible,
        "selection_allowed": selection_allowed,
        "errors": errors,
    }


def _run_sqlite(db_path: Path, workers: int, tasks_per_worker: int) -> dict:
    """Benchmark concurrent TaskClient writes through independent processes."""
    # Parent initialization ensures workers only contend on normal writes, not
    # on a first-run schema migration race.
    TaskClient(db_path, agent_id="benchmark-parent")
    payloads = [(str(db_path), worker, tasks_per_worker) for worker in range(workers)]
    started = time.perf_counter()
    context = mp.get_context("spawn")
    with context.Pool(processes=workers) as pool:
        worker_results = pool.map(_sqlite_writer, payloads)
    elapsed = time.perf_counter() - started

    total_successes = sum(item["successes"] for item in worker_results)
    errors = [error for item in worker_results for error in item["errors"]]
    stored = len(TaskClient(db_path).list(include_done=True, limit=None))
    expected = workers * tasks_per_worker
    return {
        "workers": workers,
        "tasks_per_worker": tasks_per_worker,
        "expected_writes": expected,
        "successful_writes": total_successes,
        "stored_tasks": stored,
        "errors": errors,
        "elapsed_s": elapsed,
        "writes_per_second": total_successes / elapsed if elapsed else math.inf,
        "pass": not errors and total_successes == expected and stored == expected,
    }


def _run_lockmaster(root: Path, workers: int, attempts: int) -> dict:
    """Benchmark a same-resource LockMaster claim race and read visibility."""
    payloads = [(str(root), worker, attempts) for worker in range(workers)]
    started = time.perf_counter()
    context = mp.get_context("spawn")
    with context.Pool(processes=workers) as pool:
        worker_results = pool.map(_lock_contender, payloads)
    elapsed = time.perf_counter() - started

    project = root / "shared-project"
    eager = scan_lockmaster([root])
    claims = sum(1 for item in worker_results if item["won_claim"])
    all_visible = all(item["lock_visible"] for item in worker_results)
    all_denied = all(not item["selection_allowed"] for item in worker_results)
    errors = [error for item in worker_results for error in item["errors"]]
    lock_files = [path.name for path in project.glob("LOCK*.txt")]
    return {
        "workers": workers,
        "claim_attempts_per_worker": attempts,
        "claims_succeeded": claims,
        "lock_files_after_race": lock_files,
        "eager_scan_locks": len(eager.locks_for(project)),
        "all_workers_saw_lock": all_visible,
        "all_workers_denied_selection": all_denied,
        "errors": errors,
        "elapsed_s": elapsed,
        "pass": (
            claims == 1 and len(lock_files) == 1 and all_visible and all_denied
            and not errors
        ),
    }


def _run_discovery(root: Path, project_count: int) -> dict:
    """Measure a bounded cold project scan over a synthetic local tree."""
    root.mkdir(parents=True, exist_ok=True)
    for index in range(project_count):
        project = root / f"group-{index % 10}" / f"project-{index}"
        project.mkdir(parents=True, exist_ok=True)
        (project / "TODO.md").write_text("benchmark\n", encoding="utf-8")
    config = TraversalConfig(
        roots=[root],
        levels=[
            Level("root"),
            Level("group"),
            Level("project", markers=("TODO.md",), is_work_unit=True),
        ],
        max_depth=None,
        markers=("TODO.md",),
    )
    started = time.perf_counter()
    projects = find_projects(config)
    elapsed = time.perf_counter() - started
    return {
        "synthetic_projects": project_count,
        "discovered_projects": len(projects),
        "elapsed_s": elapsed,
        "projects_per_second": len(projects) / elapsed if elapsed else math.inf,
        "pass": len(projects) == project_count,
    }


def run_benchmark(workers: int = 4, tasks_per_worker: int = 20,
                  lock_attempts: int = 1, projects: int = 200) -> dict:
    """Run both process-level checks in an isolated temporary directory."""
    if (
        workers <= 0 or tasks_per_worker <= 0
        or lock_attempts <= 0 or projects <= 0
    ):
        raise ValueError(
            "workers, tasks_per_worker, lock_attempts and projects must be > 0"
        )
    with tempfile.TemporaryDirectory(prefix="taskplan-benchmark-") as temp_dir:
        temp_root = Path(temp_dir)
        discovery_result = _run_discovery(temp_root / "discovery", projects)
        sqlite_result = _run_sqlite(
            temp_root / "tasks.sqlite3", workers, tasks_per_worker
        )
        lock_result = _run_lockmaster(temp_root / "locks", workers, lock_attempts)

    environment = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "repository": "ellmos-ai/task-master",
        "git_revision": _git_revision(),
    }
    return {
        "schema_version": "1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": environment,
        "scope": {
            "processes": workers,
            "synthetic_discovery_projects": projects,
            "shared_filesystem": "local-temporary",
            "physical_machines_tested": False,
            "network_filesystem_tested": False,
        },
        "discovery": discovery_result,
        "sqlite_taskplan": sqlite_result,
        "lockmaster": lock_result,
        "pass": (
            discovery_result["pass"]
            and sqlite_result["pass"]
            and lock_result["pass"]
        ),
        "limitations": [
            "Separate processes on one local filesystem are a contention simulation.",
            "This run does not certify SQLite WAL or file locks over SMB, NFS, or OneDrive.",
            "A physical multi-machine acceptance run requires an authorized shared filesystem.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tasks-per-worker", type=int, default=20)
    parser.add_argument("--lock-attempts", type=int, default=1)
    parser.add_argument("--projects", type=int, default=200,
                        help="Synthetic projects for the cold discovery scan")
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    args = parser.parse_args()
    try:
        result = run_benchmark(
            workers=args.workers,
            tasks_per_worker=args.tasks_per_worker,
            lock_attempts=args.lock_attempts,
            projects=args.projects,
        )
    except ValueError as exc:
        parser.error(str(exc))

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
