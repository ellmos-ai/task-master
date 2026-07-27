# -*- coding: utf-8 -*-
"""Die Konfiguration steuert Tiefe, Aufwandsdecke, Rollen, Locks und Modelle.

Nichts davon darf hartcodiert sein: Ein anderer Anwender hat andere Roots, andere
Ebenen, ein anderes Lock-System und andere Modelle.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taskplan import config as cfg


def _with_config(toml: str):
    """Patcht die geladene Konfiguration — ohne Datei im echten Home anzulegen."""
    import tomllib
    data = tomllib.loads(toml)
    return mock.patch.object(cfg, "load_config", return_value=data)


class TestSelectorConfigFromToml(unittest.TestCase):
    def test_defaults_when_no_config(self):
        with mock.patch.object(cfg, "load_config", return_value={}):
            selector = cfg.selector_config()
        self.assertTrue(selector.deep_enabled)
        self.assertEqual(selector.effort_ceiling, "medium")

    def test_surface_only_mode(self):
        """deep.enabled = false -> das alte Verhalten (nur Oberflaeche)."""
        with _with_config("[loop.deep]\nenabled = false\n"):
            selector = cfg.selector_config()
        self.assertFalse(selector.deep_enabled)

    def test_easy_ceiling(self):
        with _with_config('[loop]\neffort_ceiling = "easy"\n'):
            selector = cfg.selector_config()
        self.assertEqual(selector.allowed_efforts(), ("easy",))


class TestRoles(unittest.TestCase):
    def test_all_active_and_separate_by_default(self):
        with mock.patch.object(cfg, "load_config", return_value={}):
            roles = cfg.active_roles()
        self.assertTrue(roles["taskwriter"])
        self.assertTrue(roles["tasksolver"])
        self.assertTrue(roles["maintainer"])
        self.assertFalse(roles["combined"])

    def test_two_in_one(self):
        """Die reservierte combined-Einstellung bleibt maschinenlesbar."""
        with _with_config("[roles]\nmaintainer = false\ncombined = true\n"):
            roles = cfg.active_roles()
        self.assertFalse(roles["maintainer"])
        self.assertTrue(roles["combined"])
        self.assertTrue(roles["taskwriter"])


class TestLockProvider(unittest.TestCase):
    def test_lockmaster_is_default(self):
        with mock.patch.object(cfg, "load_config", return_value={}):
            locks = cfg.lock_config()
        self.assertEqual(locks["provider"], "lockmaster")

    def test_foreign_system_supplies_rule_paths(self):
        """Anderes System, anderes Lock-System: Der Nutzer hinterlegt Pfade zu
        seinen Regeln — sie gehen als Text in den Prompt."""
        with _with_config(
                '[locks]\nprovider = "rules"\n'
                'rule_paths = ["C:/meins/LOCK-REGELN.md"]\n'):
            locks = cfg.lock_config()
        self.assertEqual(locks["provider"], "rules")
        self.assertEqual(locks["rule_paths"], [Path("C:/meins/LOCK-REGELN.md")])


class TestModels(unittest.TestCase):
    def test_role_model_beats_default(self):
        with _with_config('[models]\ndefault = "sonnet-5"\n'
                          'tasksolver = "opus-4-8"\n'), \
                mock.patch.dict(cfg.os.environ, {"TASKPLAN_PROVIDER": ""}):
            self.assertEqual(cfg.model_for("tasksolver"), "opus-4-8")
            self.assertEqual(cfg.model_for("taskwriter"), "sonnet-5")

    def test_no_model_configured(self):
        with mock.patch.object(cfg, "load_config", return_value={}):
            self.assertEqual(cfg.model_for("tasksolver"), "")


class TestTraversalLevels(unittest.TestCase):
    def test_default_is_two_level(self):
        with mock.patch.object(cfg, "load_config", return_value={}):
            traversal = cfg.traversal_config()
        self.assertEqual([level.name for level in traversal.levels],
                         ["root", "project"])
        self.assertEqual(traversal.work_level_index, 1)

    def test_three_level_with_slot(self):
        with _with_config(
                '[[traversal.levels]]\nname = "root"\n'
                '[[traversal.levels]]\nname = "slot"\n'
                '[[traversal.levels]]\nname = "project"\n'
                'markers = ["TODO.md"]\nis_work_unit = true\n'):
            traversal = cfg.traversal_config()
        self.assertEqual([level.name for level in traversal.levels],
                         ["root", "slot", "project"])
        self.assertEqual(traversal.work_level_index, 2)

    def test_roots_come_from_existing_inventory(self):
        """Das Roots-Inventar wird nicht dupliziert — lock_roots.json ist die Quelle."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "eins").mkdir()
        roots_file = tmp / "lock_roots.json"
        roots_file.write_text(
            '{"roots": ["%s"]}' % (tmp / "eins").as_posix(), encoding="utf-8")
        with _with_config(
                '[traversal]\nroots_file = "%s"\n' % roots_file.as_posix()):
            traversal = cfg.traversal_config()
        self.assertEqual([r.name for r in traversal.roots], ["eins"])


class TestDiscoveryCacheConfig(unittest.TestCase):
    def test_default_refresh_interval_is_24_hours(self):
        with mock.patch.object(cfg, "load_config", return_value={}):
            cache = cfg.discovery_cache_config()
        self.assertEqual(cache["ttl_seconds"], 24 * 60 * 60)

    def test_refresh_interval_is_configurable(self):
        with _with_config("[traversal]\ncache_ttl_seconds = 7200\n"):
            cache = cfg.discovery_cache_config()
        self.assertEqual(cache["ttl_seconds"], 7200)


if __name__ == "__main__":
    unittest.main()
