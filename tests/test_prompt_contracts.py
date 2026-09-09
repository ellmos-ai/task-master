# -*- coding: utf-8 -*-
"""Die Prompts tragen Zusagen, die nicht still verschwinden duerfen.

Ein Prompt ist Text — und Text wird beim naechsten Ueberarbeiten gern geglaettet.
Genau das ist hier gefaehrlich: Verschwindet "DU WAEHLST NICHT AUS", waehlt das
Modell wieder selbst, und der Loop faellt in das Verhalten zurueck, das ihn
jahrelang an der Oberflaeche gehalten hat.

Diese Datei prueft die DEUTSCHE Fassung. Die englische — und die Gleichheit der
Zusagen ueber beide Sprachen — deckt `test_languages.py` ab.

WICHTIG: Hier werden NICHT die Modul-Konstanten (`taskplan.TASKSOLVER`) benutzt.
Die sind seit der Zweisprachigkeit von der BENUTZERKONFIGURATION abhaengig — ein
Test, der davon abhaengt, prueft den Rechner statt das Modul. Bei
`TASKPLAN_LANG=en` waeren 17 dieser Tests rot gewesen, obwohl am Modul nichts
kaputt ist.
"""
import unittest

from taskplan.workflows import get_workflow_prompt

TASKSOLVER = get_workflow_prompt("TASKSOLVER", "de")
TASKWRITER = get_workflow_prompt("TASKWRITER", "de")
MAINTAINER = get_workflow_prompt("MAINTAINER", "de")
OPERATOR = get_workflow_prompt("OPERATOR", "de")


class TestSolverDefersToSelector(unittest.TestCase):
    def test_solver_does_not_choose(self):
        self.assertIn("DU WÄHLST NICHT AUS", TASKSOLVER)

    def test_solver_is_told_how_to_ask(self):
        self.assertIn("python -m taskplan next", TASKSOLVER)

    def test_empty_result_is_a_valid_outcome(self):
        """Ehrlicher Leerlauf statt erfundener Arbeit."""
        self.assertIn("Erfinde keine Aufgabe", TASKSOLVER)

    def test_solver_keeps_the_goal_running(self):
        flat = _flat(TASKSOLVER)
        self.assertIn("persistierte Goal aktiv", flat)
        self.assertIn("Exit 1", flat)
        self.assertIn("Exit 3", flat)
        self.assertIn("taskplan backoff", flat)

    def test_solver_can_advance_a_stale_project_cursor(self):
        self.assertIn("skip --role tasksolver", TASKSOLVER)

    def test_local_failure_retry_limit_is_exactly_three(self):
        flat = _flat(TASKSOLVER)
        self.assertIn("RETRY-GRENZE FÜR LOKALE BÜNDELFEHLER", flat)
        self.assertIn("Nach dem dritten dokumentierten Fehlschlag", flat)
        self.assertIn("drei Versuchen", flat)
        self.assertIn("Die Aufgabe bleibt offen", flat)

    def test_cldflt_skip_continues_without_weakening_gates(self):
        flat = _flat(TASKSOLVER)
        self.assertIn("`cldflt.sys` bleibt", flat)
        self.assertIn("lokales Fail-closed-Gate", flat)
        self.assertIn("User-Locks", flat)
        self.assertIn("divergente Historien", flat)
        self.assertIn("anderer autonom bearbeitbarer Arbeit", flat)
        self.assertIn("alle erreichbaren Kandidaten", flat)
        self.assertIn("beendet weder das Goal noch den Gesamtlauf", flat)

    def test_solver_must_not_write_origin_fields(self):
        self.assertIn("assigned_to", TASKSOLVER)
        self.assertIn("created_by", TASKSOLVER)

    def test_effort_may_only_be_raised(self):
        """Nach unten stufen wuerde das Gate aushebeln, das den Solver schuetzt."""
        self.assertIn("Nach unten stufst du nie", TASKSOLVER)

    def test_solver_checks_taskplan_before_first_selector_call(self):
        self.assertIn("START-PREFLIGHT", TASKSOLVER)
        self.assertIn("python -m taskplan doctor", TASKSOLVER)
        self.assertIn("vor dem Selektor", TASKSOLVER)

    def test_model_research_has_a_cost_benefit_gate(self):
        self.assertIn("offiziellen, primären Quellen", TASKSOLVER)
        self.assertIn("Kosten-Nutzen-Vorteil", TASKSOLVER)
        self.assertIn("lokal nicht belegt", TASKSOLVER)

    def test_maintenance_exception_is_control_plane_only(self):
        self.assertIn("TASKPLAN-Control-Plane", TASKSOLVER)
        self.assertIn("KEIN allgemeines Aufräumen", _flat(TASKSOLVER))


class TestWriterClassifies(unittest.TestCase):
    def test_writer_knows_it_is_upstream(self):
        self.assertIn("OHNE DICH IST DER SOLVER BLIND", TASKWRITER)

    def test_all_four_effort_classes_are_defined(self):
        for effort in ("easy", "medium", "large", "special"):
            self.assertIn(effort, TASKWRITER)

    def test_classification_is_mandatory(self):
        self.assertIn("Keine Aufgabe ohne `effort` und `scope`", TASKWRITER)

    def test_writer_descends_into_projects(self):
        self.assertIn("Steig in die Projekte hinab", TASKWRITER)

    def test_writer_confirms_or_defers_presented_review(self):
        self.assertIn("review complete --role taskwriter", TASKWRITER)
        self.assertIn("review defer --role taskwriter", TASKWRITER)
        self.assertIn("noch KEIN Erfolg", TASKWRITER)

    def test_writer_backfills_unclassified_tasks(self):
        """Altbestand ohne effort liegt sonst fuer immer still."""
        self.assertIn("ALTBESTAND NACHSTUFEN", TASKWRITER)

    def test_doubt_raises_not_lowers(self):
        self.assertIn("stufst du **höher** ein", TASKWRITER)


def _flat(text: str) -> str:
    """Zeilenumbrueche und Einrueckung glaetten.

    Die Prompts sind auf ~100 Zeichen umbrochen — ein Satz steht also selten in
    einer Zeile. Ein Test, der stur nach der Phrase sucht, prueft die
    Zeilenlaenge statt die Zusage.
    """
    return " ".join(text.split())


class TestSharedLockModel(unittest.TestCase):
    """Alle drei Rollen tragen dieselbe Lock-Regel — sonst hebelt eine sie aus."""

    def test_reading_is_always_allowed(self):
        for prompt in (TASKSOLVER, TASKWRITER, MAINTAINER):
            self.assertIn("schützt vor Änderung, nicht vor Kenntnisnahme",
                          _flat(prompt))

    def test_lock_scope_is_the_project_not_the_pipeline(self):
        """DER Fix: Frueher legte ein Lock in EINEM Unterprojekt die ganze
        Pipeline still. Alle drei Rollen muessen das wissen."""
        for prompt in (TASKSOLVER, TASKWRITER, MAINTAINER):
            self.assertIn("nicht die ganze Pipeline", _flat(prompt))

    def test_foreign_lock_rules_are_read_not_guessed(self):
        """Fremdes Lock-System: Die Regeln kommen als Text. Lies sie — rate nicht."""
        for prompt in (TASKSOLVER, TASKWRITER, MAINTAINER):
            self.assertIn("LIES SIE", _flat(prompt).upper())


class TestMaintainerGates(unittest.TestCase):
    """Die zerstoererischste Rolle braucht die haertesten Zusagen."""

    def test_never_hard_delete(self):
        self.assertIn("NIE HART LÖSCHEN", MAINTAINER)

    def test_archive_before_truncate(self):
        self.assertIn("ARCHIVIEREN VOR KÜRZEN", MAINTAINER)

    def test_curated_content_never_loses_to_a_timestamp(self):
        self.assertIn("NIEMALS per Zeitstempel", MAINTAINER)

    def test_maintainer_neither_writes_nor_solves(self):
        self.assertIn("Keine Aufgabenerfassung", MAINTAINER)

    def test_policy_preflight_and_five_finding_classes_are_required(self):
        flat = _flat(MAINTAINER)
        self.assertIn("policy-registry resolve", flat)
        for classification in (
            "safe_autofix",
            "needs_ticket",
            "needs_system_audit",
            "needs_user_decision",
            "informational",
        ):
            self.assertIn(classification, flat)

    def test_routing_uses_stable_neighbour_surfaces(self):
        flat = _flat(MAINTAINER)
        self.assertIn("system-auditor discover", flat)
        self.assertIn("ticket_writer.py", flat)
        self.assertIn("keinen Finding-Ingest-Endpunkt", flat)

    def test_moves_require_hash_and_rollback_receipt(self):
        flat = _flat(MAINTAINER)
        for promise in ("Vorher-Pfad", "Nachher-Pfad", "SHA-256", "Rollback"):
            self.assertIn(promise, flat)


class TestRoleSeparation(unittest.TestCase):
    """Die Rollentrennung ist eine Qualitaetsgrenze, keine Organisation."""

    def test_writer_does_not_execute(self):
        self.assertIn("Keine Aufgaben-Ausführung durch den TASKWRITER", TASKWRITER)

    def test_solver_does_not_collect_or_tidy(self):
        self.assertIn("Keine Aufgaben-Erfassung", TASKSOLVER)
        self.assertIn(
            "eng begrenzten TASKPLAN-Systempreflights", _flat(TASKSOLVER)
        )


class TestOperatorIsAPersonalUnionNotAFourthRole(unittest.TestCase):
    """Der OPERATOR betreibt die drei Rollen - er ersetzt keine davon."""

    def test_operator_defers_to_each_subrole_selector(self):
        flat = _flat(OPERATOR)
        self.assertIn("DU WÄHLST NICHT AUS", flat)
        for role in ("maintainer", "taskwriter", "tasksolver"):
            self.assertIn(f"python -m taskplan next --role {role} --json", flat)
        self.assertIn("keinen Selektorlauf für die Rolle OPERATOR", flat)

    def test_subrole_prompts_stay_authoritative(self):
        flat = _flat(OPERATOR)
        self.assertIn("gilt für den Rollenschritt der Teilrollen-Prompt", flat)
        self.assertIn("Dieser Prompt hebt keine davon auf", flat)

    def test_rotation_order_is_fixed(self):
        self.assertIn("MAINTAINER -> TASKWRITER -> TASKSOLVER -> MAINTAINER", _flat(OPERATOR))

    def test_subagent_mode_alternates_and_contracts(self):
        flat = _flat(OPERATOR)
        self.assertIn("nie beide gleichzeitig", flat)
        self.assertIn("fünf Pflichtfeldern", flat)
        self.assertIn("runtime --role taskwriter --provider <provider> --field model", flat)
        self.assertIn("fail-closed in MODUS ROTATION", flat)

    def test_loop_contract_survives_idle(self):
        flat = _flat(OPERATOR)
        self.assertIn("KEINE Arbeit erfinden", flat)
        self.assertIn("backoff --role operator", flat)
        self.assertIn("genau einmal pro Operator-Lauf", flat)

    def test_pingpong_is_writesync_only(self):
        flat = _flat(OPERATOR)
        self.assertIn("WriteSync, nie ListenSync", flat)
        self.assertIn("Du startest keinen ListenSync", flat)
        self.assertIn("--ticket-kind transfer", flat)

    def test_system_audit_is_requested_via_ticket_only(self):
        flat = _flat(OPERATOR)
        self.assertIn("ANFORDERN, NIE SELBST AUSFÜHREN", flat)
        self.assertIn("system-auditor --version", flat)
        self.assertIn("system-auditor stale", flat)
        self.assertIn("Titelpräfix `system-auditor:`", flat)
        self.assertIn("existiert eines, KEIN zweites", flat)
        self.assertIn("keine Nachbildung des Audits", flat)


if __name__ == "__main__":
    unittest.main()
