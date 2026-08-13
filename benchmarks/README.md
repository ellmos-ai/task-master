# TaskPLAN Locking/Discovery-Benchmark

`taskplan_locking.py` runs a reproducible local contention check without
external services:

```powershell
python benchmarks/taskplan_locking.py --workers 4 --tasks-per-worker 20 --projects 200 --output results/benchmark_locking_YYYYMMDD.json
```

The run covers three independent surfaces:

1. a bounded cold project-discovery scan over synthetic marker projects;
2. concurrent `TaskClient` writes from separate Python processes to one SQLite
   database; and
3. a same-resource `LOCK*.txt` claim race followed by LockMaster visibility and
   selection-denial checks from every process.

The JSON result records counts, elapsed times, throughput, local Git/Python/
platform metadata, and an explicit pass/fail value. The temporary database and
lock tree are removed after the run.

This is a process-level shared-local-filesystem simulation. It is not evidence
for SQLite WAL or file-lock correctness over SMB, NFS, OneDrive, or physically
separate machines. Those environments require a separately authorized live
acceptance run and readback.
