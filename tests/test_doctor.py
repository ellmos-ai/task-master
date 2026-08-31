# -*- coding: utf-8 -*-
"""Fail-loud diagnostics for invalid project discovery configuration."""
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from taskplan import doctor
from taskplan.traversal import TraversalConfig


class TestDoctorDiscoveryConfiguration(unittest.TestCase):
    def test_zero_roots_in_hybrid_mode_is_not_reported_as_ok(self):
        output = io.StringIO()
        with mock.patch.object(doctor, "get_default_db_path", return_value=Path("tasks.db")), \
                mock.patch.object(doctor, "count_tasks_in", return_value=3), \
                mock.patch.object(doctor, "find_config_file", return_value=None), \
                mock.patch.object(doctor, "config_search_paths", return_value=[]), \
                mock.patch.object(doctor, "_known_candidates", return_value=[]), \
                mock.patch.object(
                    doctor, "traversal_config", return_value=TraversalConfig(roots=[])
                ), \
                mock.patch.object(doctor, "discovery_mode", return_value="hybrid"), \
                redirect_stdout(output):
            code = doctor.run()
        text = output.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("FEHLER", text)
        self.assertIn("keine Traversal-Roots", text)
        self.assertNotIn("OK:", text)


if __name__ == "__main__":
    unittest.main()
