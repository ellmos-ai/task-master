# -*- coding: utf-8 -*-
"""Tests fuer taskplan (uebernommen aus rinnsal/tests/test_tasks.py + Ergaenzungen)."""
import os
import unittest
from pathlib import Path
from unittest import mock

from taskplan.client import TaskClient, get_default_db_path
from taskplan import (
    TASKSOLVER,
    TASKWRITER,
    MAINTAINER,
    get_workflow_prompt,
    get_workflow_prompt_path,
    list_workflows,
)


class TestTaskClient(unittest.TestCase):
    def setUp(self):
        self.client = TaskClient(db_path=":memory:", agent_id="test")

    def test_add_and_get(self):
        task = self.client.add("Feature X", description="Details", priority="high",
                               tags="dev")
        self.assertEqual(task['status'], 'open')
        fetched = self.client.get(task['id'])
        self.assertEqual(fetched['title'], "Feature X")
        self.assertEqual(fetched['priority'], "high")
        self.assertEqual(fetched['agent_id'], "test")
        self.assertEqual(fetched['tags'], "dev")

    def test_add_invalid_priority_raises(self):
        with self.assertRaises(ValueError):
            self.client.add("Task", priority="urgent")

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.client.get(999))

    def test_list_orders_by_semantic_priority(self):
        self.client.add("low", priority="low")
        self.client.add("critical", priority="critical")
        self.client.add("medium", priority="medium")
        self.client.add("high", priority="high")
        titles = [t['title'] for t in self.client.list()]
        self.assertEqual(titles, ["critical", "high", "medium", "low"])

    def test_list_excludes_done_and_cancelled_by_default(self):
        t1 = self.client.add("bleibt")
        t2 = self.client.add("erledigt")
        t3 = self.client.add("storniert")
        self.client.done(t2['id'])
        self.client.cancel(t3['id'])
        titles = [t['title'] for t in self.client.list()]
        self.assertEqual(titles, ["bleibt"])
        titles_all = [t['title'] for t in self.client.list(include_done=True)]
        self.assertEqual(len(titles_all), 3)

    def test_list_accepts_none_for_an_unbounded_result(self):
        for i in range(55):
            self.client.add(f"Task {i}")

        self.assertEqual(len(self.client.list(limit=None)), 55)

    def test_status_transitions(self):
        task = self.client.add("Task")
        tid = task['id']
        self.assertTrue(self.client.activate(tid))
        self.assertEqual(self.client.get(tid)['status'], 'active')
        self.assertTrue(self.client.done(tid))
        done = self.client.get(tid)
        self.assertEqual(done['status'], 'done')
        self.assertIsNotNone(done['done_at'])
        self.assertTrue(self.client.reopen(tid))
        reopened = self.client.get(tid)
        self.assertEqual(reopened['status'], 'open')
        self.assertIsNone(reopened['done_at'])

    def test_status_change_on_missing_task_returns_false(self):
        self.assertFalse(self.client.done(999))
        self.assertFalse(self.client.activate(999))

    def test_update_fields(self):
        task = self.client.add("Alt", priority="low")
        ok = self.client.update(task['id'], title="Neu", priority="critical",
                                description="Beschreibung", tags="x,y")
        self.assertTrue(ok)
        updated = self.client.get(task['id'])
        self.assertEqual(updated['title'], "Neu")
        self.assertEqual(updated['priority'], "critical")
        self.assertEqual(updated['description'], "Beschreibung")
        self.assertEqual(updated['tags'], "x,y")

    def test_update_invalid_priority_raises(self):
        task = self.client.add("Task")
        with self.assertRaises(ValueError):
            self.client.update(task['id'], priority="asap")

    def test_delete(self):
        task = self.client.add("Wegwerf")
        self.assertTrue(self.client.delete(task['id']))
        self.assertIsNone(self.client.get(task['id']))
        self.assertFalse(self.client.delete(task['id']))

    def test_count(self):
        self.client.add("a")
        t2 = self.client.add("b")
        self.client.done(t2['id'])
        counts = self.client.count()
        self.assertEqual(counts['open'], 1)
        self.assertEqual(counts['done'], 1)
        self.assertEqual(counts['total'], 2)


class TestDefaultDbPath(unittest.TestCase):
    """Aufloesung des Default-DB-Pfads.

    WICHTIG — Test-Isolation: `get_default_db_path()` liest die Umgebung UND
    eine eventuell vorhandene Konfigurationsdatei des Benutzers. Ohne das Patchen
    von `configured_db_path` haengen diese Tests davon ab, ob auf dem Rechner
    zufaellig eine `~/.taskplan/taskplan.toml` liegt — sie schlugen genau deshalb
    fehl, als auf dem Entwicklungssystem eine angelegt wurde. Ein Test, der von
    der realen Benutzerkonfiguration abhaengt, prueft nicht das Modul, sondern
    den Rechner.
    """

    def setUp(self):
        self._taskplan_db = os.environ.pop("TASKPLAN_DB", None)
        self._rinnsal_db = os.environ.pop("RINNSAL_DB", None)
        # Benutzerkonfiguration ausblenden -> deterministische Aufloesung
        self._patcher = mock.patch("taskplan.client.configured_db_path",
                                   return_value="")
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        for key, value in (("TASKPLAN_DB", self._taskplan_db),
                           ("RINNSAL_DB", self._rinnsal_db)):
            if value is not None:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)

    def test_env_taskplan_db_wins(self):
        os.environ["TASKPLAN_DB"] = "C:/tmp/tp.db"
        os.environ["RINNSAL_DB"] = "C:/tmp/rn.db"
        self.assertEqual(Path(get_default_db_path()), Path("C:/tmp/tp.db"))

    def test_env_rinnsal_db_fallback(self):
        os.environ["RINNSAL_DB"] = "C:/tmp/rn.db"
        self.assertEqual(Path(get_default_db_path()), Path("C:/tmp/rn.db"))

    def test_home_default(self):
        path = Path(get_default_db_path())
        self.assertEqual(path.name, "taskplan.db")
        self.assertEqual(path.parent, Path.home() / ".taskplan")


class TestConfigPrecedence(unittest.TestCase):
    """Die Konfiguration steht VOR RINNSAL_DB, aber HINTER TASKPLAN_DB.

    Wer TASKPLAN ausdruecklich konfiguriert, meint das auch — eine geerbte
    Alt-Variable darf ihn nicht ueberstimmen. Ein explizit gesetztes
    TASKPLAN_DB dagegen schon (Notausstieg fuer einen einzelnen Lauf).
    """

    def setUp(self):
        self._taskplan_db = os.environ.pop("TASKPLAN_DB", None)
        self._rinnsal_db = os.environ.pop("RINNSAL_DB", None)

    def tearDown(self):
        for key, value in (("TASKPLAN_DB", self._taskplan_db),
                           ("RINNSAL_DB", self._rinnsal_db)):
            if value is not None:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)

    def test_config_beats_rinnsal_db(self):
        os.environ["RINNSAL_DB"] = "C:/tmp/legacy.db"
        with mock.patch("taskplan.client.configured_db_path",
                        return_value="C:/tmp/configured.db"):
            self.assertEqual(Path(get_default_db_path()),
                             Path("C:/tmp/configured.db"))

    def test_taskplan_db_beats_config(self):
        os.environ["TASKPLAN_DB"] = "C:/tmp/explicit.db"
        with mock.patch("taskplan.client.configured_db_path",
                        return_value="C:/tmp/configured.db"):
            self.assertEqual(Path(get_default_db_path()),
                             Path("C:/tmp/explicit.db"))


class TestTasksApi(unittest.TestCase):
    def setUp(self):
        from taskplan import api
        self.api = api
        self.api.init(db_path=":memory:", agent_id="api-test")

    def test_add_list_done_roundtrip(self):
        self.api.add("Task 1", priority="high")
        self.api.add("Task 2", priority="low")
        tasks = self.api.list()
        self.assertEqual(len(tasks), 2)
        nxt = self.api.next_task()
        self.assertEqual(nxt['title'], "Task 1")
        self.assertTrue(self.api.done(nxt['id']))
        self.assertEqual(len(self.api.list()), 1)

    def test_active_tasks(self):
        task = self.api.add("aktiv")
        self.api.activate(task['id'])
        active = self.api.active_tasks()
        self.assertEqual([t['title'] for t in active], ["aktiv"])

    def test_add_from_ticket_sets_ticket_tag(self):
        task = self.api.add_from_ticket("T-20260711-01", "Aus Ticket abgeleitet",
                                        priority="high", tags="infra")
        fetched = self.api.get(task['id'])
        self.assertEqual(fetched['tags'], "infra,ticket:T-20260711-01")
        self.assertEqual(fetched['status'], 'open')

    def test_add_from_ticket_without_tags(self):
        task = self.api.add_from_ticket("T-20260711-02", "Nur Ticket-Ref")
        self.assertEqual(self.api.get(task['id'])['tags'],
                         "ticket:T-20260711-02")


class TestWorkflowPrompts(unittest.TestCase):
    def test_workflows_are_imported_from_taskplan(self):
        self.assertEqual(list_workflows(),
                         ("TASKSOLVER", "TASKWRITER", "MAINTAINER"))
        # Sprachexplizit: die Modul-Konstanten haengen seit der
        # Zweisprachigkeit von der Benutzerkonfiguration ab.
        self.assertIn("ROLLE: Du bist der TASKSOLVER",
                      get_workflow_prompt("TASKSOLVER", "de"))
        self.assertIn("ROLE: You are the TASKSOLVER",
                      get_workflow_prompt("TASKSOLVER", "en"))

    def test_maintainer_carries_its_hardest_gate(self):
        """Der MAINTAINER ist die zerstoererischste Rolle (Dateien verschieben,
        Listen leeren, Logs kuerzen). Sein Kerngate darf in KEINER Sprache
        wegredigiert werden."""
        self.assertIn("NIE HART LÖSCHEN", get_workflow_prompt("MAINTAINER", "de"))
        self.assertIn("NEVER HARD-DELETE", get_workflow_prompt("MAINTAINER", "en"))

    def test_prompt_lookup_is_case_insensitive(self):
        self.assertEqual(get_workflow_prompt("tasksolver", "de"),
                         get_workflow_prompt("TASKSOLVER", "de"))
        self.assertEqual(get_workflow_prompt(" TaskWriter ", "en"),
                         get_workflow_prompt("TASKWRITER", "en"))

    def test_prompt_paths_are_real_utf8_files(self):
        for name in list_workflows():
            path = get_workflow_prompt_path(name)
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_text(encoding="utf-8"),
                             get_workflow_prompt(name))

    def test_unknown_workflow_fails_clearly(self):
        with self.assertRaisesRegex(KeyError, "Unbekannter TASKPLAN-Workflow"):
            get_workflow_prompt("unknown")


if __name__ == "__main__":
    unittest.main()
