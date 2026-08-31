# -*- coding: utf-8 -*-
"""Die Prompts liegen zweisprachig vor — und BEIDE Fassungen tragen dieselben
Zusagen.

Eine Uebersetzung ist genau die Gelegenheit, bei der eine Zusage still
verlorengeht: Man glaettet einen Satz, und ploetzlich waehlt der Solver wieder
selbst. Deshalb pruefen diese Tests jede Rolle in JEDER Sprache.
"""
import os
import unittest
from unittest import mock

from taskplan.workflows import (
    AVAILABLE_LANGS, DEFAULT_LANG, get_workflow_prompt,
    get_workflow_prompt_path, list_workflows, resolve_lang,
)


def _flat(text: str) -> str:
    return " ".join(text.split())


class TestBothLanguagesExist(unittest.TestCase):
    def test_every_role_in_every_language(self):
        for lang in AVAILABLE_LANGS:
            for role in list_workflows():
                text = get_workflow_prompt(role, lang)
                self.assertGreater(len(text), 500,
                                   f"{role}/{lang} ist verdaechtig kurz")

    def test_paths_resolve_for_external_launchers(self):
        """Starter brauchen einen realen Dateipfad, keinen Zip-Eintrag."""
        for lang in AVAILABLE_LANGS:
            for role in list_workflows():
                path = get_workflow_prompt_path(role, lang)
                self.assertTrue(path.is_file())
                self.assertEqual(path.parent.name, lang)


class TestLanguageResolution(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.pop("TASKPLAN_LANG", None)

    def tearDown(self):
        if self._env is not None:
            os.environ["TASKPLAN_LANG"] = self._env
        else:
            os.environ.pop("TASKPLAN_LANG", None)

    def test_env_wins(self):
        os.environ["TASKPLAN_LANG"] = "en"
        self.assertEqual(resolve_lang(), "en")

    def test_explicit_argument_beats_env(self):
        os.environ["TASKPLAN_LANG"] = "en"
        self.assertEqual(resolve_lang("de"), "de")

    def test_config_is_used(self):
        with mock.patch("taskplan.config.load_config",
                        return_value={"language": {"prompts": "de"}}):
            self.assertEqual(resolve_lang(), "de")

    def test_default_is_english(self):
        """Nutzerneutral: Der Default ist Englisch."""
        with mock.patch("taskplan.config.load_config", return_value={}):
            self.assertEqual(resolve_lang(), DEFAULT_LANG)
            self.assertEqual(DEFAULT_LANG, "en")

    def test_unknown_language_falls_back_loudly(self):
        """Ein Tippfehler darf nicht still eine andere Sprache liefern."""
        os.environ["TASKPLAN_LANG"] = "klingon"
        self.assertEqual(resolve_lang(), "en")


class TestPromiseParityAcrossLanguages(unittest.TestCase):
    """Jede Zusage muss in BEIDEN Fassungen stehen. Sonst haette man eine
    Rolle, die auf Englisch etwas anderes verspricht als auf Deutsch."""

    PROMISES = {
        "TASKSOLVER": {
            "de": ["DU WÄHLST NICHT AUS", "python -m taskplan next",
                   "Erfinde keine Aufgabe", "Nach unten stufst du nie",
                   "START-PREFLIGHT", "offiziellen, primären Quellen",
                   "Kosten-Nutzen-Vorteil", "Exit 3", "taskplan backoff",
                   "skip --role tasksolver", "Nach dem dritten dokumentierten Fehlschlag",
                   "lokales Fail-closed-Gate", "alle erreichbaren Kandidaten"],
            "en": ["YOU DO NOT CHOOSE", "python -m taskplan next",
                   "Do not invent a task", "You never lower it",
                   "START PREFLIGHT", "official, primary sources",
                   "cost-benefit advantage", "Exit 3", "taskplan backoff",
                   "skip --role tasksolver", "After the third documented failure",
                   "local fail-closed gate", "every reachable candidate"],
        },
        "TASKWRITER": {
            "de": ["OHNE DICH IST DER SOLVER BLIND", "Steig in die Projekte hinab",
                   "Keine Aufgabe ohne `effort` und `scope`", "ALTBESTAND NACHSTUFEN",
                   "review complete --role taskwriter",
                   "review defer --role taskwriter"],
            "en": ["WITHOUT YOU THE SOLVER IS BLIND", "Descend into the projects",
                    "No task without `effort` and `scope`",
                    "BACKFILL THE CLASSIFICATION",
                    "review complete --role taskwriter",
                    "review defer --role taskwriter"],
        },
        "MAINTAINER": {
            "de": ["NIE HART LÖSCHEN", "ARCHIVIEREN VOR KÜRZEN",
                   "NIEMALS per Zeitstempel", "policy-registry resolve",
                    "maintainer-plan", "MAINTAINER_FINGERPRINT",
                    "system-auditor discover", "keinen Finding-Ingest-Endpunkt",
                    "review complete --role maintainer",
                    "review defer --role maintainer"],
            "en": ["NEVER HARD-DELETE", "ARCHIVE BEFORE TRUNCATING",
                   "NEVER decide by timestamp", "policy-registry resolve",
                    "maintainer-plan", "MAINTAINER_FINGERPRINT",
                    "system-auditor discover", "no finding-ingest endpoint",
                    "review complete --role maintainer",
                    "review defer --role maintainer"],
        },
    }

    def test_every_promise_survives_translation(self):
        for role, by_lang in self.PROMISES.items():
            for lang, phrases in by_lang.items():
                text = _flat(get_workflow_prompt(role, lang))
                for phrase in phrases:
                    self.assertIn(phrase, text,
                                  f"{role}/{lang}: Zusage {phrase!r} fehlt")

    def test_lock_model_in_both_languages(self):
        """Die Lock-Regel darf in keiner Sprache fehlen — sonst haette eine
        Fassung ein loechrigeres Schutzmodell als die andere."""
        needles = {
            "de": "schützt vor Änderung, nicht vor Kenntnisnahme",
            "en": "protects against change, not against knowledge",
        }
        for role in list_workflows():
            for lang, needle in needles.items():
                self.assertIn(needle, _flat(get_workflow_prompt(role, lang)),
                              f"{role}/{lang}: Lock-Regel fehlt")

    def test_pipeline_scope_rule_in_both_languages(self):
        needles = {"de": "nicht die ganze Pipeline",
                   "en": "not the whole pipeline"}
        for role in list_workflows():
            for lang, needle in needles.items():
                self.assertIn(needle, _flat(get_workflow_prompt(role, lang)),
                              f"{role}/{lang}: Lock-Scope-Regel fehlt")

    def test_foreign_lock_rules_are_read_in_both_languages(self):
        needles = {"de": "LIES SIE", "en": "READ THEM"}
        for role in list_workflows():
            for lang, needle in needles.items():
                self.assertIn(needle, _flat(get_workflow_prompt(role, lang)).upper(),
                              f"{role}/{lang}: 'lies die fremden Regeln' fehlt")


if __name__ == "__main__":
    unittest.main()
