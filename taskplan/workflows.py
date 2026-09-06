# -*- coding: utf-8 -*-
"""Importierbare TASKPLAN-Workflow-Prompts — zweisprachig.

Die drei Rollen (TASKSOLVER, TASKWRITER, MAINTAINER) und ihre Personalunion
OPERATOR liegen als Paketressourcen in `prompts/<lang>/` vor. Sie sind damit
sowohl aus Python importierbar als auch fuer CLI-/Batch-Starter als reale
Dateien aufloesbar.

Sprachwahl:
    1. expliziter `lang`-Parameter
    2. ENV `TASKPLAN_LANG`
    3. Konfiguration: `[language] prompts = "de"` in taskplan.toml
    4. DEFAULT_LANG (en)

Der Default ist Englisch, weil das Modul nutzerneutral ist. Wer auf Deutsch
arbeitet, setzt `prompts = "de"` in der Konfiguration.

Fehlt eine Uebersetzung, wird auf `FALLBACK_LANG` zurueckgegriffen — mit einer
Warnung. Ein stiller Sprachwechsel waere schlimmer als ein lauter: Der Prompt
ist der Vertrag der Rolle, und niemand sollte raetseln muessen, welchen er
gerade bekommen hat.
"""
from importlib import resources
import os
import sys
from pathlib import Path

_WORKFLOW_FILES = {
    "TASKSOLVER": "TASKSOLVER.txt",
    "TASKWRITER": "TASKWRITER.txt",
    "MAINTAINER": "MAINTAINER.txt",
    "OPERATOR": "OPERATOR.txt",
}

AVAILABLE_LANGS = ("de", "en")
DEFAULT_LANG = "en"
FALLBACK_LANG = "en"


def _normalize_name(name: str) -> str:
    normalized = name.strip().upper()
    if normalized not in _WORKFLOW_FILES:
        available = ", ".join(_WORKFLOW_FILES)
        raise KeyError(f"Unbekannter TASKPLAN-Workflow {name!r}; verfügbar: {available}")
    return normalized


def _configured_lang() -> str:
    """Sprache aus ENV oder Konfiguration."""
    env = os.environ.get("TASKPLAN_LANG", "").strip().lower()
    if env:
        return env
    try:
        from .config import load_config
        section = load_config().get("language", {}) or {}
        configured = str(section.get("prompts", "")).strip().lower()
        if configured:
            return configured
    except Exception:      # pragma: no cover - Konfiguration darf nie blockieren
        pass
    return DEFAULT_LANG


def resolve_lang(lang: str | None = None) -> str:
    """Die tatsaechlich zu verwendende Sprache."""
    chosen = (lang or _configured_lang()).strip().lower()
    if chosen not in AVAILABLE_LANGS:
        print(f"[taskplan] Unbekannte Prompt-Sprache {chosen!r}; "
              f"verfügbar: {', '.join(AVAILABLE_LANGS)}. "
              f"Nutze {FALLBACK_LANG!r}.", file=sys.stderr)
        return FALLBACK_LANG
    return chosen


def list_workflows() -> tuple[str, ...]:
    """Liefert die Namen aller gebündelten TASKPLAN-Workflows."""
    return tuple(_WORKFLOW_FILES)


def list_languages() -> tuple[str, ...]:
    return AVAILABLE_LANGS


def _resource(name: str, lang: str):
    filename = _WORKFLOW_FILES[name]
    return resources.files(f"taskplan.prompts.{lang}").joinpath(filename)


def get_workflow_prompt(name: str, lang: str | None = None) -> str:
    """Lädt einen Workflow-Prompt als UTF-8-Text."""
    normalized = _normalize_name(name)
    chosen = resolve_lang(lang)
    try:
        return _resource(normalized, chosen).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        if chosen == FALLBACK_LANG:
            raise
        # Lauter Fallback: Der Prompt ist der Vertrag der Rolle — ein stiller
        # Sprachwechsel waere schlimmer als ein gemeldeter.
        print(f"[taskplan] {normalized} fehlt in {chosen!r}; "
              f"nutze {FALLBACK_LANG!r}.", file=sys.stderr)
        return _resource(normalized, FALLBACK_LANG).read_text(encoding="utf-8")


def get_workflow_prompt_path(name: str, lang: str | None = None) -> Path:
    """Liefert den realen Dateipfad eines installierten Workflow-Prompts.

    Setuptools installiert TASKPLAN unverpackt, sodass die Ressource regulär im
    Dateisystem liegt. Ein exotischer Zip-Importer wird bewusst abgelehnt, weil
    externe Starter einen dauerhaft gültigen Pfad benötigen.
    """
    normalized = _normalize_name(name)
    chosen = resolve_lang(lang)
    resource = _resource(normalized, chosen)
    try:
        path = Path(os.fspath(resource)).resolve()
    except TypeError as exc:  # pragma: no cover - nur bei Zip-Importern
        raise RuntimeError(
            "Der Workflow-Prompt liegt nicht als reale Datei vor"
        ) from exc
    if not path.is_file():
        if chosen == FALLBACK_LANG:
            raise FileNotFoundError(path)
        print(f"[taskplan] {normalized} fehlt in {chosen!r}; "
              f"nutze {FALLBACK_LANG!r}.", file=sys.stderr)
        return Path(os.fspath(_resource(normalized, FALLBACK_LANG))).resolve()
    return path


# Modul-Konstanten in der konfigurierten Sprache (bequemer Zugriff).
TASKSOLVER = get_workflow_prompt("TASKSOLVER")
TASKWRITER = get_workflow_prompt("TASKWRITER")
MAINTAINER = get_workflow_prompt("MAINTAINER")
OPERATOR = get_workflow_prompt("OPERATOR")
