"""Small process-level regression checks for the locking benchmark."""

import unittest

from benchmarks.taskplan_locking import run_benchmark


class TestBenchmarkLocking(unittest.TestCase):
    def test_process_benchmark_verifies_discovery_sqlite_and_lockmaster(self):
        result = run_benchmark(workers=2, tasks_per_worker=2, projects=5)

        self.assertIs(result["pass"], True)
        self.assertEqual(result["discovery"]["discovered_projects"], 5)
        self.assertEqual(result["sqlite_taskplan"]["stored_tasks"], 4)
        self.assertEqual(result["lockmaster"]["claims_succeeded"], 1)
        self.assertIs(result["scope"]["physical_machines_tested"], False)

    def test_process_benchmark_rejects_non_positive_parameters(self):
        with self.assertRaisesRegex(ValueError, "must be > 0"):
            run_benchmark(workers=0)


if __name__ == "__main__":
    unittest.main()
