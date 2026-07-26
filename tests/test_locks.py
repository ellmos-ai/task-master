# -*- coding: utf-8 -*-
"""Das Lock-Modell: drei Achsen, projektgenauer Scope, fremde Systeme."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from taskplan.locks import (
    CREATE, MODIFY, READ,
    LockView, build_lock_view, load_rule_texts, scan_lockmaster,
)


def _touch(path: Path, text: str = "lock") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestThreeAxes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.locked = self.tmp / "root" / "gesperrt"
        self.free = self.tmp / "root" / "frei"
        _touch(self.locked / "LOCK.txt")
        self.free.mkdir(parents=True)
        self.view = scan_lockmaster([self.tmp / "root"])

    def test_read_is_always_allowed(self):
        """Ein Lock schuetzt vor Aenderung, nicht vor Kenntnisnahme.
        Genau diese Unterscheidung fehlte — Analysen wurden mitblockiert."""
        self.assertTrue(self.view.allows(self.locked, READ))

    def test_create_is_allowed_in_locked_dir(self):
        """Eine NEUE Datei kollidiert nicht mit fremder Arbeit an bestehenden."""
        self.assertTrue(self.view.allows(self.locked, CREATE))

    def test_modify_is_blocked_in_locked_dir(self):
        self.assertFalse(self.view.allows(self.locked, MODIFY))

    def test_modify_is_allowed_in_free_dir(self):
        self.assertTrue(self.view.allows(self.free, MODIFY))


class TestScopeIsProjectNotPipeline(unittest.TestCase):
    """DER Kernfehler: Ein Lock in EINEM Unterprojekt hat die ganze Pipeline
    lahmgelegt. Er darf nur sein eigenes Projekt sperren."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pipeline = self.tmp / ".RESEARCH"
        self.locked_project = self.pipeline / "RH"
        self.sibling = self.pipeline / "CRM"
        _touch(self.locked_project / "LOCK.txt")
        self.sibling.mkdir(parents=True)
        self.view = scan_lockmaster([self.pipeline])

    def test_locked_project_is_blocked(self):
        self.assertFalse(self.view.allows(self.locked_project, MODIFY))

    def test_sibling_project_stays_free(self):
        self.assertTrue(self.view.allows(self.sibling, MODIFY),
                        "Der Lock im Nachbarprojekt hat dieses mitgesperrt!")

    def test_pipeline_root_stays_free(self):
        self.assertTrue(self.view.allows(self.pipeline, MODIFY),
                        "Ein Lock im Unterprojekt hat die ganze Pipeline gesperrt!")

    def test_lock_above_blocks_below(self):
        """Umgekehrt gilt: ein Lock WEITER OBEN sperrt alles darunter."""
        _touch(self.pipeline / "LOCK.txt")
        view = scan_lockmaster([self.pipeline])
        self.assertFalse(view.allows(self.sibling, MODIFY))


class TestUserLockIsAbsolute(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "root" / "projekt"
        _touch(self.project / "LOCK.user.zenodo-upload.txt")
        self.view = scan_lockmaster([self.tmp / "root"])

    def test_user_lock_blocks_modify(self):
        self.assertFalse(self.view.allows(self.project, MODIFY))

    def test_user_lock_blocks_even_create(self):
        """Beim User-Lock endet die Nachsicht — auch neue Dateien sind tabu."""
        self.assertFalse(self.view.allows(self.project, CREATE))

    def test_user_lock_still_allows_read(self):
        self.assertTrue(self.view.allows(self.project, READ))

    def test_user_lock_never_expires(self):
        """Auch nominell abgelaufen bleibt er aktiv — nur der Nutzer entfernt ihn."""
        view = scan_lockmaster([self.tmp / "root"], ttl_hours=0)
        self.assertFalse(view.allows(self.project, MODIFY))


class TestExpiry(unittest.TestCase):
    """Der 24h-Verfall ist nur das Sicherheitsnetz — wer lockt, gibt selbst frei.
    Ein vergessener Lock darf das System aber nicht dauerhaft blockieren."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "root" / "projekt"

    def _age_lock(self, name: str, hours: float) -> None:
        lock = self.project / name
        _touch(lock)
        old = time.time() - hours * 3600
        os.utime(lock, (old, old))

    def test_fresh_lock_blocks(self):
        self._age_lock("LOCK.txt", hours=1)
        view = scan_lockmaster([self.tmp / "root"])
        self.assertFalse(view.allows(self.project, MODIFY))

    def test_expired_lock_does_not_block(self):
        self._age_lock("LOCK.txt", hours=30)   # aelter als die 24h-Frist
        view = scan_lockmaster([self.tmp / "root"])
        self.assertTrue(view.allows(self.project, MODIFY))

    def test_expired_lock_blocks_autonomous_selection(self):
        """Ein stale Lock darf nicht still erneut autonome Arbeit anziehen.

        Nach manueller Pruefung bleibt MODIFY gemaess TTL moeglich, aber der
        Selektor soll bis zur Klaerung einen anderen sicheren Kandidaten nehmen.
        """
        self._age_lock("LOCK.txt", hours=30)
        view = scan_lockmaster([self.tmp / "root"])
        self.assertFalse(view.allows_selection(self.project))

    def test_expired_user_lock_still_blocks(self):
        """Ein User-Lock verfaellt NIE — auch nach Wochen nicht."""
        self._age_lock("LOCK.user.txt", hours=1000)
        view = scan_lockmaster([self.tmp / "root"])
        self.assertFalse(view.allows(self.project, MODIFY))


class TestPermissionsJson(unittest.TestCase):
    """deny > ask > allow. `ask` wird fuer einen autonomen Lauf wie `deny`
    behandelt — wer nicht fragen kann, darf nicht handeln."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "root" / "projekt"
        self.project.mkdir(parents=True)

    def _view(self, rules: str) -> LockView:
        _touch(self.project / "LOCK.permissions.json", rules)
        return scan_lockmaster([self.tmp / "root"])

    def test_deny_blocks(self):
        view = self._view('{"deny": ["modify"]}')
        self.assertFalse(view.allows(self.project, MODIFY))

    def test_allow_permits_despite_lock(self):
        _touch(self.project / "LOCK.txt")
        view = self._view('{"allow": ["modify"]}')
        self.assertTrue(view.allows(self.project, MODIFY))
        self.assertTrue(view.allows_selection(self.project))

    def test_ask_is_treated_as_deny_when_autonomous(self):
        view = self._view('{"ask": ["modify"]}')
        self.assertFalse(view.allows(self.project, MODIFY))

    def test_deny_beats_allow(self):
        view = self._view('{"allow": ["modify"], "deny": ["modify"]}')
        self.assertFalse(view.allows(self.project, MODIFY))

    def test_broken_json_does_not_crash(self):
        view = self._view('{kaputt')
        self.assertTrue(view.allows(self.project, MODIFY))


class TestForeignLockSystem(unittest.TestCase):
    """Andere Systeme, andere Lock-Systeme: Ist es nicht unser Schema, wird
    NICHT geraten. Der Nutzer hinterlegt Regelpfade — die gehen als Text in den
    Prompt, und das LLM wendet sie an."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.rules = self.tmp / "meine-lock-regeln.md"
        self.rules.write_text("Regel: Ordner mit .busy nicht anfassen.",
                              encoding="utf-8")

    def test_rules_are_passed_through_as_text(self):
        texts = load_rule_texts([self.rules])
        self.assertEqual(len(texts), 1)
        self.assertIn(".busy", texts[0])

    def test_missing_rule_file_is_skipped(self):
        texts = load_rule_texts([self.tmp / "gibtsnicht.md"])
        self.assertEqual(texts, [])

    def test_provider_rules_does_not_scan(self):
        """Provider 'rules' wertet NICHTS aus — es raet nicht an fremder Semantik."""
        _touch(self.tmp / "projekt" / "LOCK.txt")
        view = build_lock_view("rules", roots=[self.tmp],
                               rule_paths=[self.rules])
        self.assertEqual(view.locks, [])
        self.assertEqual(len(view.extra_rules), 1)

    def test_provider_lockmaster_enforces(self):
        """Auf das VERHALTEN pruefen, nicht auf die interne Liste: Der Provider
        wertet bedarfsgesteuert aus, `locks` ist vorab absichtlich leer."""
        project = self.tmp / "projekt"
        _touch(project / "LOCK.txt")
        view = build_lock_view("lockmaster", roots=[self.tmp])
        self.assertFalse(view.allows(project, MODIFY))
        self.assertTrue(view.allows(project, READ))

    def test_provider_lockmaster_eager_fills_the_list(self):
        _touch(self.tmp / "projekt" / "LOCK.txt")
        view = build_lock_view("lockmaster", roots=[self.tmp], eager=True)
        self.assertEqual(len(view.locks), 1)

    def test_provider_none_is_permissive(self):
        _touch(self.tmp / "projekt" / "LOCK.txt")
        view = build_lock_view("none", roots=[self.tmp])
        self.assertTrue(view.allows(self.tmp / "projekt", MODIFY))


if __name__ == "__main__":
    unittest.main()


class TestLazyLockView(unittest.TestCase):
    """Bedarfsgesteuerte Pruefung: dieselben Ergebnisse, ohne Vollscan.

    Der Vollscan ueber einen Cloud-Ordner mit 16 Roots dauert Minuten — und ist
    verschwendet, weil am Ende nur die paar Projekte gefragt sind, die auch
    Aufgaben haben.
    """

    def setUp(self):
        from taskplan.locks import LazyLockView
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / ".RESEARCH"
        self.locked = self.root / "RH"
        self.sibling = self.root / "CRM"
        _touch(self.locked / "LOCK.txt")
        self.sibling.mkdir(parents=True)
        self.view = LazyLockView([self.root])

    def test_same_verdict_as_full_scan(self):
        eager = scan_lockmaster([self.root])
        for directory in (self.locked, self.sibling, self.root):
            for action in (READ, CREATE, MODIFY):
                self.assertEqual(
                    self.view.allows(directory, action),
                    eager.allows(directory, action),
                    f"Lazy und Vollscan sind sich uneinig: {directory.name}/{action}")

    def test_lock_above_is_found(self):
        _touch(self.root / "LOCK.txt")
        from taskplan.locks import LazyLockView
        view = LazyLockView([self.root])
        self.assertFalse(view.allows(self.sibling, MODIFY))

    def test_stale_lock_blocks_selection_but_not_manual_modify(self):
        lock = self.locked / "LOCK.txt"
        old = time.time() - 30 * 3600
        os.utime(lock, (old, old))
        from taskplan.locks import LazyLockView
        view = LazyLockView([self.root])

        self.assertTrue(view.allows(self.locked, MODIFY))
        self.assertFalse(view.allows_selection(self.locked))

    def test_does_not_look_beyond_the_root(self):
        """Ein Lock OBERHALB der Root geht uns nichts an — sonst wandert die
        Suche ins Nirgendwo hinauf."""
        _touch(self.tmp / "LOCK.txt")
        from taskplan.locks import LazyLockView
        view = LazyLockView([self.root])
        self.assertTrue(view.allows(self.sibling, MODIFY))
