# -*- coding: utf-8 -*-
"""Der Aufgaben-Revolver: zurueckstellen, ohne Etiketten zu verfaelschen.

Ausgangsbefund (2026-08-23): Der Projekt-Cursor ist zu grob, wenn genau EINE
Aufgabe blockiert ist. Sie stand nach jedem Rotationszyklus wieder vorn, weil
die Store-Sortierung stabil ist. Der einzige verbleibende Hebel eines Agenten
waren die Etiketten der Aufgabe — ``effort`` hochstufen, ``priority`` senken,
``status`` faelschen. Alle drei behaupten etwas Falsches ueber die Aufgabe.

Diese Tests halten das Gegenmittel fest: Reihenfolge ist Rotationszustand,
nicht Aufgabeneigenschaft.
"""
import json
import tempfile
import unittest
from pathlib import Path

from taskplan.client import TaskClient
from taskplan.locks import LockView
from taskplan.rotation import (
    defer_task,
    deferred_tasks,
    last_project,
    remember_project,
    undefer_task,
)
from taskplan.selector import SelectorConfig, next_bundle

from test_selector import FakeStore, task


class TestRevolverState(unittest.TestCase):
    """Die Trommel: geordnet, rollenbezogen, verlustfrei."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "rotation.json"

    def tearDown(self):
        self._dir.cleanup()

    def test_defer_appends_in_order(self):
        self.assertTrue(defer_task(self.path, "tasksolver", 7))
        self.assertTrue(defer_task(self.path, "tasksolver", 3))
        self.assertEqual(deferred_tasks(self.path, "tasksolver"), [7, 3])

    def test_repeated_defer_moves_to_the_back_without_duplicate(self):
        for task_id in (1, 2, 3):
            defer_task(self.path, "tasksolver", task_id)
        defer_task(self.path, "tasksolver", 1)
        self.assertEqual(deferred_tasks(self.path, "tasksolver"), [2, 3, 1])

    def test_drum_is_role_scoped(self):
        defer_task(self.path, "tasksolver", 42)
        self.assertEqual(deferred_tasks(self.path, "maintainer"), [])

    def test_project_skip_preserves_the_drum(self):
        """Regression: ``remember_project`` ueberschrieb den Rolleneintrag.

        Ein Projekt-Skip haette die Aufgaben-Trommel stillschweigend geleert —
        die zurueckgestellten Aufgaben waeren beim naechsten Lauf wieder vorn
        gewesen, ohne dass es jemand bemerkt haette.
        """
        defer_task(self.path, "tasksolver", 2118)
        remember_project(self.path, "tasksolver", "/projekte/a")
        self.assertEqual(deferred_tasks(self.path, "tasksolver"), [2118])
        self.assertEqual(last_project(self.path, "tasksolver"), "/projekte/a")

    def test_defer_preserves_the_project_cursor(self):
        remember_project(self.path, "tasksolver", "/projekte/a")
        defer_task(self.path, "tasksolver", 5)
        self.assertEqual(last_project(self.path, "tasksolver"), "/projekte/a")

    def test_undefer_removes_and_is_idempotent(self):
        defer_task(self.path, "tasksolver", 5)
        defer_task(self.path, "tasksolver", 6)
        self.assertTrue(undefer_task(self.path, "tasksolver", 5))
        self.assertEqual(deferred_tasks(self.path, "tasksolver"), [6])
        self.assertTrue(undefer_task(self.path, "tasksolver", 5))
        self.assertEqual(deferred_tasks(self.path, "tasksolver"), [6])

    def test_corrupt_drum_is_not_a_selector_error(self):
        self.path.write_text(
            json.dumps({"roles": {"tasksolver": {"deferred_tasks": "kaputt"}}}),
            encoding="utf-8",
        )
        self.assertEqual(deferred_tasks(self.path, "tasksolver"), [])


class TestRevolverSelection(unittest.TestCase):
    """Die Auswahl: zurueckgestellt heisst zuletzt, nicht nie."""

    def setUp(self):
        self.config = SelectorConfig(effort_ceiling="medium",
                                     easy_first_globally=True)
        self.locks = LockView()

    def test_deferred_task_yields_to_a_fresh_one(self):
        store = FakeStore([
            task("Blockiert", project="/p/a", root=".SOFTWARE"),
            task("Frei", project="/p/b", root=".RESEARCH"),
        ])
        bundle = next_bundle(self.config, store, self.locks,
                             deferred_task_ids=[1])
        self.assertIsNotNone(bundle)
        self.assertEqual([t["title"] for t in bundle.tasks], ["Frei"])

    def test_deferred_task_does_not_slip_in_through_bundling(self):
        """Ein zurueckgestelltes Item darf nicht per Projektbuendelung zurueck.

        ``_bundle_from`` nimmt ALLE Aufgaben desselben Projekts bis
        ``max_bundle_size``. Ohne Vorfilter waere die blockierte Aufgabe ueber
        ihren freien Nachbarn wieder im Buendel gelandet.
        """
        store = FakeStore([
            task("Blockiert", project="/p/a", root=".SOFTWARE"),
            task("Frei", project="/p/a", root=".SOFTWARE"),
        ])
        bundle = next_bundle(self.config, store, self.locks,
                             deferred_task_ids=[1])
        self.assertEqual([t["title"] for t in bundle.tasks], ["Frei"])

    def test_full_drum_serves_the_longest_waiting_first(self):
        """Ist alles zurueckgestellt, verhungert niemand — die Trommel dreht."""
        store = FakeStore([
            task("Zuerst zurueckgestellt", project="/p/a", root=".SOFTWARE"),
            task("Danach zurueckgestellt", project="/p/b", root=".RESEARCH"),
        ])
        bundle = next_bundle(self.config, store, self.locks,
                             deferred_task_ids=[2, 1])
        self.assertIsNotNone(bundle, "Voll besetzte Trommel darf nicht leerlaufen")
        self.assertEqual(bundle.tasks[0]["title"], "Danach zurueckgestellt")

    def test_revolver_also_covers_surface_tasks(self):
        """Wurzelaufgaben haben kein Projekt — dort ist der Revolver der einzige Hebel."""
        store = FakeStore([
            task("Wurzel blockiert"),
            task("Wurzel frei"),
        ])
        bundle = next_bundle(self.config, store, self.locks,
                             deferred_task_ids=[1])
        self.assertEqual([t["title"] for t in bundle.tasks], ["Wurzel frei"])

    def test_without_a_drum_nothing_changes(self):
        store = FakeStore([task("Erste", project="/p/a", root=".SOFTWARE")])
        for drum in (None, []):
            bundle = next_bundle(self.config, store, self.locks,
                                 deferred_task_ids=drum)
            self.assertEqual([t["title"] for t in bundle.tasks], ["Erste"])

    def test_stale_ids_in_the_drum_are_harmless(self):
        store = FakeStore([task("Einzige", project="/p/a", root=".SOFTWARE")])
        bundle = next_bundle(self.config, store, self.locks,
                             deferred_task_ids=[999, 1000])
        self.assertEqual([t["title"] for t in bundle.tasks], ["Einzige"])


class TestUpdatedAtOrdering(unittest.TestCase):
    """Grundfairness im Store: zuletzt angefasst kommt zuletzt wieder.

    WICHTIG — warum hier gestempelt und nicht geschlafen wird:
    ``datetime.now()`` hat unter Windows rund 15,6 ms Granularitaet. Zwei
    schnell aufeinanderfolgende Schreibvorgaenge bekommen denselben
    ISO-String, und die Reihenfolge waere zufaellig. Diese Tests setzen die
    Stempel deshalb deterministisch: Geprueft wird die SORTIERUNG, nicht die
    Aufloesung der Systemuhr.

    Fuer den Betrieb folgt daraus die Rollenverteilung: ``updated_at`` ist
    Grundfairness gegen Verhungern, der explizite Revolver ist die
    verlaessliche Zusage.
    """

    @staticmethod
    def _stamp(client, task_id, value):
        conn = client._get_conn()
        conn.execute("UPDATE rinnsal_tasks SET updated_at = ? WHERE id = ?",
                     (value, task_id))
        conn.commit()

    def test_least_recently_touched_comes_first(self):
        client = TaskClient(db_path=":memory:", agent_id="test")
        first = client.add("Zuerst angelegt", priority="medium")["id"]
        second = client.add("Danach angelegt", priority="medium")["id"]

        self._stamp(client, first, "2026-06-01T00:00:00")
        self._stamp(client, second, "2026-01-01T00:00:00")

        order = [t["id"] for t in client.list(status="open", limit=None)]
        self.assertEqual(order, [second, first],
                         "Die zuletzt angefasste Aufgabe muss hinten stehen")

    def test_update_refreshes_the_timestamp(self):
        client = TaskClient(db_path=":memory:", agent_id="test")
        task_id = client.add("Angefasst", priority="medium")["id"]
        self._stamp(client, task_id, "2020-01-01T00:00:00")

        self.assertTrue(client.update(task_id, description="angefasst"))
        self.assertGreater(client.get(task_id)["updated_at"],
                           "2020-01-01T00:00:00",
                           "update() muss den Zeitstempel nachziehen")

    def test_priority_still_outranks_recency(self):
        client = TaskClient(db_path=":memory:", agent_id="test")
        low = client.add("Unwichtig", priority="low")["id"]
        high = client.add("Dringend", priority="high")["id"]

        self._stamp(client, low, "2020-01-01T00:00:00")
        self._stamp(client, high, "2026-12-31T00:00:00")

        order = [t["id"] for t in client.list(status="open", limit=None)]
        self.assertEqual(order, [high, low],
                         "Prioritaet bleibt die primaere Dimension")

    def test_missing_timestamp_falls_back_to_creation(self):
        client = TaskClient(db_path=":memory:", agent_id="test")
        first = client.add("Ohne Stempel", priority="medium")["id"]
        second = client.add("Mit Stempel", priority="medium")["id"]

        self._stamp(client, first, "")
        self._stamp(client, second, "2026-12-31T00:00:00")

        order = [t["id"] for t in client.list(status="open", limit=None)]
        self.assertEqual(order, [first, second],
                         "Leerer Stempel darf nicht nach vorn oder hinten kippen")


if __name__ == "__main__":
    unittest.main()
