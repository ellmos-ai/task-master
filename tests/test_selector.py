# -*- coding: utf-8 -*-
"""Der Selektor — der Kern des Umbaus.

Er ersetzt die Prompt-Prosa durch einen deterministischen Zustandsautomaten.
Als Text war die Reihenfolge eine Bitte, die das Modell jedes Mal neu auslegte;
der Loop lief deshalb leer, statt in die Tiefe zu eskalieren.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path

from taskplan.locks import LockView, scan_lockmaster
from taskplan.selector import (
    DEEP, SURFACE, SelectorConfig, next_bundle,
)


class FakeStore:
    """In-Memory-Store. Der Selektor kennt kein SQL — deshalb testbar ohne DB."""

    def __init__(self, tasks):
        for i, task in enumerate(tasks):
            task.setdefault("id", i + 1)
            task.setdefault("status", "open")
            task.setdefault("scope", "local")
            task.setdefault("project_path", "")
            task.setdefault("root_id", "")
            task.setdefault("effort", "")
        self.tasks = tasks

    def list(self, status=None, effort=None, limit=50, **kwargs):
        out = self.tasks
        if status:
            out = [t for t in out if t["status"] == status]
        if effort is not None:
            out = [t for t in out if t["effort"] == effort]
        return out if limit is None else out[:limit]

    def get(self, task_id):
        return next((t for t in self.tasks if t["id"] == task_id), None)


def task(title, effort="easy", project="", root="", scope="local"):
    return {"title": title, "effort": effort, "project_path": project,
            "root_id": root, "scope": scope}


class TestEffortIsPrimary(unittest.TestCase):
    """DIE Kernregel: easy wird GLOBAL erschoepft, bevor irgendwo medium
    angefasst wird. Nicht easy->medium innerhalb einer Root."""

    def setUp(self):
        self.config = SelectorConfig()
        self.locks = LockView()

    def test_easy_in_other_root_beats_medium_in_this_root(self):
        store = FakeStore([
            task("M in .AI", effort="medium", project="/ai/x", root=".AI"),
            task("E in .SW", effort="easy", project="/sw/y", root=".SW"),
        ])
        bundle = next_bundle(self.config, store, self.locks)
        self.assertEqual(bundle.effort, "easy")
        self.assertEqual(bundle.root_id, ".SW",
                         "Der Selektor nahm medium, obwohl anderswo easy offen war")

    def test_medium_only_when_no_easy_left_anywhere(self):
        store = FakeStore([
            task("M", effort="medium", project="/ai/x", root=".AI"),
        ])
        bundle = next_bundle(self.config, store, self.locks)
        self.assertEqual(bundle.effort, "medium")

    def test_nothing_left_returns_none(self):
        """Ehrlicher Leerlauf statt erfundener Arbeit."""
        self.assertIsNone(next_bundle(self.config, FakeStore([]), self.locks))


class TestSurfaceBeforeDeep(unittest.TestCase):
    def setUp(self):
        self.config = SelectorConfig()
        self.locks = LockView()

    def test_surface_task_wins_over_deep_task(self):
        store = FakeStore([
            task("Tief", effort="easy", project="/ai/proj", root=".AI"),
            task("Oberflaeche", effort="easy", project="", root=".AI"),
        ])
        bundle = next_bundle(self.config, store, self.locks)
        self.assertEqual(bundle.mode, SURFACE)
        self.assertEqual(bundle.tasks[0]["title"], "Oberflaeche")

    def test_deep_when_surface_is_clean(self):
        store = FakeStore([
            task("Tief", effort="easy", project="/ai/proj", root=".AI"),
        ])
        bundle = next_bundle(self.config, store, self.locks)
        self.assertEqual(bundle.mode, DEEP)

    def test_deep_disabled_falls_back_to_next_effort(self):
        """deep_enabled=False = das alte Verhalten (nur Oberflaeche)."""
        config = SelectorConfig(deep_enabled=False)
        store = FakeStore([
            task("Tief easy", effort="easy", project="/ai/p", root=".AI"),
            task("Oberflaeche medium", effort="medium", project="", root=".AI"),
        ])
        bundle = next_bundle(config, store, self.locks)
        self.assertEqual(bundle.mode, SURFACE)
        self.assertEqual(bundle.effort, "medium")


class TestGates(unittest.TestCase):
    """large/special und scope=central sind NIE autonom — das Gate sitzt im
    Code, nicht im Prompt."""

    def setUp(self):
        self.config = SelectorConfig()
        self.locks = LockView()

    def test_large_is_never_selected(self):
        store = FakeStore([task("Gross", effort="large", root=".AI")])
        self.assertIsNone(next_bundle(self.config, store, self.locks))

    def test_special_is_never_selected(self):
        store = FakeStore([task("Spezial", effort="special", root=".AI")])
        self.assertIsNone(next_bundle(self.config, store, self.locks))

    def test_central_scope_is_never_selected(self):
        store = FakeStore([task("Zentral", effort="easy", scope="central",
                                root=".AI")])
        self.assertIsNone(next_bundle(self.config, store, self.locks))

    def test_unclassified_is_not_treated_as_easy(self):
        """Ohne effort NICHT anfassen — lieber liegen lassen als raten."""
        store = FakeStore([task("Unklar", effort="", root=".AI")])
        self.assertIsNone(next_bundle(self.config, store, self.locks))

    def test_ceiling_easy_blocks_medium(self):
        config = SelectorConfig(effort_ceiling="easy")
        store = FakeStore([task("M", effort="medium", root=".AI")])
        self.assertIsNone(next_bundle(config, store, self.locks))


class TestDependencies(unittest.TestCase):
    """Offene Vorstufen duerfen den Solver nicht in eine Sackgasse schicken."""

    def setUp(self):
        self.config = SelectorConfig()
        self.locks = LockView()

    def test_open_dependency_skips_task_and_selects_next_project(self):
        prerequisite = task("Rate-Entscheid", effort="special",
                            project="/p/connes", root=".RESEARCH")
        prerequisite["id"] = 307
        blocked = task("Paper finalisieren", effort="medium",
                       project="/p/connes", root=".RESEARCH")
        blocked.update(id=308, tags="stable-id=CH-TEX-004;depends-on=307")
        next_project = task("Naechstes ausfuehrbares Projekt", effort="medium",
                            project="/p/next", root=".RESEARCH")
        next_project["id"] = 400

        bundle = next_bundle(
            self.config,
            FakeStore([blocked, prerequisite, next_project]),
            self.locks,
        )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle.project_path, "/p/next")
        self.assertEqual(bundle.tasks[0]["id"], 400)

    def test_task_becomes_selectable_after_dependency_is_done(self):
        prerequisite = task("Rate-Entscheid", effort="special",
                            project="/p/connes", root=".RESEARCH")
        prerequisite.update(id=307, status="done")
        dependent = task("Paper finalisieren", effort="medium",
                         project="/p/connes", root=".RESEARCH")
        dependent.update(id=308, tags="depends-on=307;stable-id=CH-TEX-004")
        next_project = task("Naechstes Projekt", effort="medium",
                            project="/p/next", root=".RESEARCH")
        next_project["id"] = 400

        bundle = next_bundle(
            self.config,
            FakeStore([dependent, prerequisite, next_project]),
            self.locks,
        )

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle.project_path, "/p/connes")
        self.assertEqual(bundle.tasks[0]["id"], 308)


class TestLockAwareness(unittest.TestCase):
    """Ein Lock trifft SEIN Projekt — nicht die Nachbarn, nicht die Pipeline."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pipeline = self.tmp / ".RESEARCH"
        self.locked = self.pipeline / "RH"
        self.free = self.pipeline / "CRM"
        (self.locked).mkdir(parents=True)
        (self.free).mkdir(parents=True)
        (self.locked / "LOCK.txt").write_text("busy", encoding="utf-8")
        self.locks = scan_lockmaster([self.pipeline])
        self.config = SelectorConfig()

    def test_locked_project_is_skipped(self):
        store = FakeStore([
            task("Im gesperrten", effort="easy",
                 project=str(self.locked), root=".RESEARCH"),
        ])
        self.assertIsNone(next_bundle(self.config, store, self.locks))

    def test_sibling_project_still_worked_on(self):
        """DER Fix: Frueher legte EIN Lock die ganze Pipeline stumm."""
        store = FakeStore([
            task("Im gesperrten", effort="easy",
                 project=str(self.locked), root=".RESEARCH"),
            task("Im freien", effort="easy",
                 project=str(self.free), root=".RESEARCH"),
        ])
        bundle = next_bundle(self.config, store, self.locks)
        self.assertIsNotNone(bundle, "Der Lock hat die ganze Pipeline gesperrt!")
        self.assertEqual(bundle.tasks[0]["title"], "Im freien")

    def test_stale_locked_project_is_skipped_for_free_sibling(self):
        old = time.time() - 30 * 3600
        os.utime(self.locked / "LOCK.txt", (old, old))
        locks = scan_lockmaster([self.pipeline])
        store = FakeStore([
            task("Im stale gesperrten", effort="easy",
                 project=str(self.locked), root=".RESEARCH"),
            task("Im freien", effort="easy",
                 project=str(self.free), root=".RESEARCH"),
        ])

        bundle = next_bundle(self.config, store, locks)

        self.assertIsNotNone(bundle)
        self.assertEqual(bundle.tasks[0]["title"], "Im freien")


class TestBundling(unittest.TestCase):
    def setUp(self):
        self.config = SelectorConfig()
        self.locks = LockView()

    def test_bundle_stays_within_one_project(self):
        store = FakeStore([
            task("A1", effort="easy", project="/p/a", root=".AI"),
            task("A2", effort="easy", project="/p/a", root=".AI"),
            task("B1", effort="easy", project="/p/b", root=".AI"),
        ])
        bundle = next_bundle(self.config, store, self.locks)
        self.assertEqual(len(bundle), 2)
        self.assertTrue(all(t["project_path"] == "/p/a" for t in bundle.tasks),
                        "Das Buendel mischt unabhaengige Projekte")

    def test_bundle_respects_max_size(self):
        config = SelectorConfig(max_bundle_size=2)
        store = FakeStore([
            task(f"T{i}", effort="easy", project="/p/a", root=".AI")
            for i in range(5)
        ])
        bundle = next_bundle(config, store, self.locks)
        self.assertEqual(len(bundle), 2)


if __name__ == "__main__":
    unittest.main()


class TestWriterHasItsOwnSelection(unittest.TestCase):
    """Die Luecke, die der TASKWRITER-Loop selbst aufgedeckt hat (2026-07-14).

    Er bekam dieselbe Auswahl wie der TASKSOLVER — und damit systematisch
    NICHTS. Der Solver waehlt nur, was klassifiziert ist; der Writer ist aber
    genau derjenige, der einstuft. Sobald der Solver-Vorrat leer war, hatte der
    Writer nie wieder etwas zu tun. Ein Loop, der sich selbst aushungert.
    """

    def setUp(self):
        self.locks = LockView()
        self.config = SelectorConfig()

    def test_writer_gets_the_unclassified_ones(self):
        store = FakeStore([
            task("Eingestuft", effort="easy", project="/p/a", root=".AI"),
            task("Uneingestuft", effort="", project="/p/b", root=".SW"),
        ])
        bundle = next_bundle(self.config, store, self.locks, role="taskwriter")
        self.assertIsNotNone(bundle)
        self.assertEqual(bundle.tasks[0]["title"], "Uneingestuft")

    def test_solver_ignores_exactly_those(self):
        """Gegenprobe: Was der Writer sucht, meidet der Solver."""
        store = FakeStore([task("Uneingestuft", effort="", project="/p/b", root=".SW")])
        self.assertIsNone(next_bundle(self.config, store, self.locks,
                                      role="tasksolver"))
        self.assertIsNotNone(next_bundle(self.config, store, self.locks,
                                         role="taskwriter"))

    def test_writer_is_not_blocked_by_the_effort_ceiling(self):
        """Der Writer fuehrt nichts aus — er beschreibt nur. Das Aufwands-Gate
        schuetzt den Solver, nicht ihn."""
        config = SelectorConfig(effort_ceiling="easy")
        store = FakeStore([task("Gross und uneingestuft", effort="",
                                project="/p/x", root=".AI")])
        self.assertIsNotNone(next_bundle(config, store, self.locks,
                                         role="taskwriter"))

    def test_writer_still_respects_locks(self):
        """Aber der Lock-Scope gilt auch fuer ihn: In ein gesperrtes Projekt
        schreibt er keine Steuerdateien."""
        tmp = Path(tempfile.mkdtemp())
        locked = tmp / "root" / "gesperrt"
        locked.mkdir(parents=True)
        (locked / "LOCK.txt").write_text("busy", encoding="utf-8")
        locks = scan_lockmaster([tmp / "root"])
        store = FakeStore([task("Im gesperrten", effort="",
                                project=str(locked), root=".X")])
        self.assertIsNone(next_bundle(self.config, store, locks,
                                      role="taskwriter"))

    def test_writer_finds_a_project_without_any_tasks(self):
        """Ist alles eingestuft, sucht er ein Projekt, das noch GAR KEINE
        Aufgaben hat — dort ist der Rueckstand per Definition unerfasst."""
        from taskplan.traversal import Project

        store = FakeStore([task("Alles klar", effort="easy",
                                project="/p/bekannt", root=".AI")])
        config = SelectorConfig(projects=[
            Project(path=Path("/p/bekannt"), root_id=".AI"),
            Project(path=Path("/p/unberuehrt"), root_id=".SW"),
        ])
        bundle = next_bundle(config, store, self.locks, role="taskwriter")
        self.assertIsNotNone(bundle)
        self.assertIn("unberuehrt", bundle.project_path)
        self.assertEqual(len(bundle.tasks), 0, "Ein leeres Projekt hat keine Tasks")

    def test_writer_reports_honest_emptiness(self):
        """Kein Projekt uebrig -> None. Keine erfundene Arbeit."""
        store = FakeStore([task("X", effort="easy", project="/p/a", root=".AI")])
        self.assertIsNone(next_bundle(SelectorConfig(), store, self.locks,
                                      role="taskwriter"))

    def test_writer_remembers_projects_beyond_1000_tasks(self):
        """Die Projekthistorie darf nicht am alten 1000er-Fenster enden."""
        from taskplan.traversal import Project

        history = [task(f"Alt {i}", project=f"/p/alt/{i}")
                   for i in range(1000)]
        history.append(task("Ziel bereits erfasst", project="/p/ziel"))
        config = SelectorConfig(projects=[
            Project(path=Path("/p/ziel"), root_id=".AI"),
        ])

        self.assertIsNone(next_bundle(config, FakeStore(history), self.locks,
                                      role="taskwriter"))


class TestMaintainerDoesNotCollideWithSolver(unittest.TestCase):
    """Die Kollision, die der MAINTAINER-Loop selbst gemeldet hat (2026-07-14).

    Er fiel in den TASKSOLVER-Zweig und bekam DASSELBE Projekt zugewiesen.
    2 von 2 Zuweisungen kollidierten: Der Solver lockte das Projekt, der
    Maintainer stand Sekunden vor dem Schreiben vor einem fremden Lock.

    Das war KEINE Race Condition. Es war eine garantierte Kollision.
    """

    def setUp(self):
        from taskplan.traversal import Project
        self.locks = LockView()
        self.projects = [
            Project(path=Path("/p/a"), root_id=".AI"),
            Project(path=Path("/p/b"), root_id=".SW"),
        ]
        self.config = SelectorConfig(projects=self.projects)

    def test_maintainer_avoids_a_project_someone_works_on(self):
        """Der Solver hat /p/a geclaimt -> der Maintainer nimmt /p/b."""
        t = task("Solver arbeitet dran", effort="easy", project="/p/a", root=".AI")
        t["status"] = "active"
        store = FakeStore([t])
        bundle = next_bundle(self.config, store, self.locks, role="maintainer")
        self.assertIsNotNone(bundle)
        self.assertIn("b", bundle.project_path)

    def test_claimed_but_not_yet_locked_also_counts_as_busy(self):
        """DAS Zeitfenster, das die Kollisionen erzeugte: Der Solver hat
        geclaimt (assigned_to), seinen Lock aber noch nicht gesetzt. Der Lock
        allein als Kriterium haette hier nicht gereicht."""
        t = task("Geclaimt", effort="easy", project="/p/a", root=".AI")
        t["assigned_to"] = "claude"
        store = FakeStore([t])
        bundle = next_bundle(self.config, store, self.locks, role="maintainer")
        self.assertIsNotNone(bundle)
        self.assertNotIn("/p/a", bundle.project_path.replace("\\", "/"))

    def test_maintainer_and_solver_never_get_the_same_project(self):
        """Die Kernzusage — als Test festgehalten."""
        t = task("Offen", effort="easy", project="/p/a", root=".AI")
        t["status"] = "active"
        store = FakeStore([t])
        solver = next_bundle(self.config, store, self.locks, role="tasksolver")
        maint = next_bundle(self.config, store, self.locks, role="maintainer")
        if solver and maint:
            self.assertNotEqual(
                Path(solver.project_path).as_posix().lower(),
                Path(maint.project_path).as_posix().lower(),
                "Solver und Maintainer bekamen DASSELBE Projekt!")

    def test_maintainer_respects_locks(self):
        tmp = Path(tempfile.mkdtemp())
        locked = tmp / "root" / "gesperrt"
        locked.mkdir(parents=True)
        (locked / "LOCK.txt").write_text("busy", encoding="utf-8")
        from taskplan.traversal import Project
        config = SelectorConfig(projects=[Project(path=locked, root_id=".X")])
        locks = scan_lockmaster([tmp / "root"])
        self.assertIsNone(next_bundle(config, FakeStore([]), locks,
                                      role="maintainer"))

    def test_maintainer_needs_no_tasks(self):
        """Er raeumt auf — dafuer braucht er keine erfasste Aufgabe."""
        bundle = next_bundle(self.config, FakeStore([]), self.locks,
                             role="maintainer")
        self.assertIsNotNone(bundle)
        self.assertEqual(len(bundle.tasks), 0)

    def test_maintainer_sees_busy_project_beyond_1000_tasks(self):
        """Ein alter Verlaufs-Cutoff darf keine aktive Arbeit verstecken."""
        from taskplan.traversal import Project

        history = [task(f"Alt {i}", project=f"/p/alt/{i}")
                   for i in range(1000)]
        busy = task("Noch aktiv", project="/p/beschaeftigt")
        busy["status"] = "active"
        history.append(busy)
        config = SelectorConfig(projects=[
            Project(path=Path("/p/beschaeftigt"), root_id=".AI"),
            Project(path=Path("/p/frei"), root_id=".SW"),
        ])

        bundle = next_bundle(config, FakeStore(history), self.locks,
                             role="maintainer")

        self.assertIsNotNone(bundle)
        self.assertEqual(Path(bundle.project_path).as_posix(), "/p/frei")


class TestAllThreeRolesGetDifferentWork(unittest.TestCase):
    """Die Rollen duerfen sich nicht ins Gehege kommen — und zwar KEINE zwei.

    Der erste Fix entkoppelte Solver und Maintainer — und verschob die
    Kollision auf Writer/Maintainer. Beide liefen von oben durch dieselbe
    Projektliste.

    Die Loesung ist inhaltlich, nicht mechanisch: Die beiden suchen gar nicht
    dasselbe. Der Writer sucht UNBERUEHRTE Projekte (dort fehlt die Erfassung),
    der Maintainer BERUEHRTE (dort ist Doku-Drift entstanden). Disjunkte
    Mengen — ohne kuenstlichen Trick.
    """

    def setUp(self):
        from taskplan.traversal import Project
        self.locks = LockView()
        self.projects = [
            Project(path=Path("/p/beruehrt"), root_id=".AI"),
            Project(path=Path("/p/unberuehrt"), root_id=".SW"),
        ]
        self.config = SelectorConfig(projects=self.projects)
        # /p/beruehrt hat eine (erledigte) Aufgabe -> dort war schon jemand.
        done = task("Erledigt", effort="easy", project="/p/beruehrt", root=".AI")
        done["status"] = "done"
        self.store = FakeStore([done])

    def test_writer_takes_the_untouched_one(self):
        b = next_bundle(self.config, self.store, self.locks, role="taskwriter")
        self.assertIsNotNone(b)
        self.assertIn("unberuehrt", b.project_path)

    def test_maintainer_takes_the_touched_one(self):
        """Wo schon gearbeitet wurde, ist Doku-Drift entstanden."""
        b = next_bundle(self.config, self.store, self.locks, role="maintainer")
        self.assertIsNotNone(b)
        self.assertIn("beruehrt", b.project_path)
        self.assertNotIn("unberuehrt", b.project_path)

    def test_writer_and_maintainer_never_collide(self):
        w = next_bundle(self.config, self.store, self.locks, role="taskwriter")
        m = next_bundle(self.config, self.store, self.locks, role="maintainer")
        self.assertNotEqual(
            Path(w.project_path).as_posix().lower(),
            Path(m.project_path).as_posix().lower(),
            "Writer und Maintainer bekamen DASSELBE Projekt!")

    def test_maintainer_falls_back_to_untouched_if_nothing_else(self):
        """Gibt es nur unberuehrte Projekte, nimmt er die auch — lieber das als
        Leerlauf."""
        config = SelectorConfig(projects=[self.projects[1]])   # nur unberuehrt
        b = next_bundle(config, FakeStore([]), self.locks, role="maintainer")
        self.assertIsNotNone(b)
