# -*- coding: utf-8 -*-
"""TASKPLAN-Konfiguration.

Die Datenbankwahl gehoert in die Konfiguration, nicht in den Code und nicht in
den Starter: Unser SQLite-Backend ist der empfohlene Default, aber ein anderer
Anwender soll TASKPLAN auch dann benutzen koennen, wenn seine Aufgaben woanders
liegen.

Suchreihenfolge der Konfigurationsdatei:
    1. ENV TASKPLAN_CONFIG (expliziter Pfad)
    2. ./taskplan.toml      (Projekt-lokal)
    3. ~/.taskplan/taskplan.toml  (Benutzer-weit)

TOML wird nur gelesen, wenn `tomllib` verfuegbar ist (Python >= 3.11). Auf
aelteren Versionen bleibt das Modul benutzbar — die Konfiguration wird dann
ignoriert und es gelten ENV-Variablen und Defaults. Zero dependencies bleibt
Zero dependencies.
"""
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import tomllib  # Python >= 3.11
except ImportError:  # pragma: no cover - nur auf Python 3.10
    tomllib = None  # type: ignore[assignment]


def config_search_paths() -> list[Path]:
    """Die Orte, an denen nach einer Konfiguration gesucht wird — in dieser Reihenfolge."""
    explicit = os.environ.get("TASKPLAN_CONFIG", "")
    paths = []
    if explicit:
        paths.append(Path(explicit).expanduser())
    paths.append(Path.cwd() / "taskplan.toml")
    paths.append(Path.home() / ".taskplan" / "taskplan.toml")
    return paths


def find_config_file() -> Optional[Path]:
    """Erste existierende Konfigurationsdatei — oder None."""
    for path in config_search_paths():
        if path.is_file():
            return path
    return None


def load_config() -> Dict[str, Any]:
    """Laedt die Konfiguration. Leeres Dict, wenn keine da ist oder TOML fehlt."""
    if tomllib is None:
        return {}
    path = find_config_file()
    if path is None:
        return {}
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except (OSError, ValueError):
        # Eine kaputte Konfiguration darf das Modul nicht lahmlegen — sie wird
        # ignoriert, damit ENV und Default weiterhin greifen.
        return {}


def configured_db_path() -> str:
    """Der in der Konfiguration hinterlegte DB-Pfad — oder "" wenn keiner."""
    storage = load_config().get("storage", {})
    path = storage.get("path", "") if isinstance(storage, dict) else ""
    return str(Path(path).expanduser()) if path else ""


def selector_config():
    """Baut die SelectorConfig aus der Konfigurationsdatei."""
    from .selector import SelectorConfig

    config = load_config()
    loop = config.get("loop", {}) or {}
    deep = loop.get("deep", {}) or {}
    return SelectorConfig(
        deep_enabled=bool(deep.get("enabled", True)),
        effort_ceiling=str(loop.get("effort_ceiling", "medium")),
        easy_first_globally=bool(deep.get("easy_first_globally", True)),
        projects_per_dive=int(deep.get("projects_per_dive", 1)),
        max_bundle_size=int(loop.get("max_bundle_size", 3)),
        review_pool_enabled=review_pool_config().enabled,
    )


def rotation_state_file() -> Path:
    """Pfad fuer den persistenten Projekt-Cursor.

    Der Zustand gehoert weder in ein Projekt noch in die Task-Tabelle. Er ist
    deshalb benutzerweit konfigurierbar und liegt standardmaessig unter
    ``~/.taskplan``.
    """
    loop = load_config().get("loop", {}) or {}
    raw = str(loop.get("rotation_state_file", "~/.taskplan/rotation-state.json"))
    return Path(raw).expanduser()


def review_pool_config():
    """Hostlokale Wiedervorlagepolitik für TASKWRITER und MAINTAINER.

    Kaputte Benutzerwerte degradieren auf dokumentierte sichere Defaults,
    statt den ganzen Selector-Prozess beim Start unbrauchbar zu machen.
    """
    from .review_pool import REVIEW_EFFORTS, ReviewPolicy

    section = load_config().get("review_pool", {}) or {}
    if not isinstance(section, dict):
        section = {}

    defaults = {
        "review_interval_seconds": 7 * 24 * 60 * 60,
        "retry_interval_seconds": 60 * 60,
        "presentation_lease_seconds": 15 * 60,
    }

    def interval(name: str) -> int:
        try:
            value = int(section.get(name, defaults[name]))
        except (TypeError, ValueError):
            return defaults[name]
        if name == "presentation_lease_seconds":
            return value if value > 0 else defaults[name]
        return value if value >= 0 else defaults[name]

    effort = str(section.get("default_effort", "easy")).strip().lower()
    if effort not in REVIEW_EFFORTS:
        effort = "easy"
    raw_excludes = section.get("exclude", [])
    excludes = (
        tuple(str(item) for item in raw_excludes)
        if isinstance(raw_excludes, list)
        else ()
    )
    return ReviewPolicy(
        enabled=bool(section.get("enabled", True)),
        review_interval_seconds=interval("review_interval_seconds"),
        retry_interval_seconds=interval("retry_interval_seconds"),
        presentation_lease_seconds=interval("presentation_lease_seconds"),
        default_effort=effort,
        exclude=excludes,
    )


def prompt_language() -> str:
    """Sprache der Rollen-Prompts.

    Reihenfolge: ENV `TASKPLAN_LANG` > `[language] prompts` > Default (en).
    Der Default ist Englisch, weil das Modul nutzerneutral ist.
    """
    from .workflows import resolve_lang
    return resolve_lang()


def discovery_mode() -> str:
    """Wie werden Projekte gefunden?

    "auto"    nur ueber Marker (TODO.md, .git, ...). Schnell, aber blind fuer
              Systeme, die andere Konventionen haben.
    "manual"  NUR die gepflegte Registry (projects.json). Fuer Installationen,
              deren Struktur sich nicht aus Dateinamen ableiten laesst.
    "hybrid"  beides — Automatik plus Registry, dedupliziert. Der Default:
              die Automatik traegt, die Registry korrigiert.
    """
    section = load_config().get("traversal", {}) or {}
    mode = str(section.get("discovery", "hybrid")).lower()
    return mode if mode in ("auto", "manual", "hybrid") else "hybrid"


def registry_file() -> str:
    """Pfad der manuellen Projektliste ("" = Default ~/.taskplan/projects.json)."""
    section = load_config().get("traversal", {}) or {}
    return str(section.get("registry_file", ""))


def traversal_config():
    """Baut die TraversalConfig aus der Konfigurationsdatei.

    Das Roots-Inventar wird NICHT dupliziert: `[traversal] roots_file` zeigt auf
    eine bestehende Roots-Liste (z. B. `lock_roots.json`). Zwei Listen wuerden
    unweigerlich auseinanderlaufen.
    """
    from .traversal import DEFAULT_SKIP_DIRS, Level, TraversalConfig, find_roots

    config = load_config()
    section = config.get("traversal", {}) or {}

    roots = []
    roots_file = section.get("roots_file", "")
    if roots_file:
        roots = find_roots(Path(roots_file).expanduser())
    roots += [Path(r).expanduser() for r in section.get("roots", []) or []]

    raw_levels = section.get("levels") or []
    if raw_levels:
        levels = [Level(name=str(level.get("name", f"level{i}")),
                        markers=tuple(level.get("markers", []) or []),
                        is_work_unit=bool(level.get("is_work_unit", False)))
                  for i, level in enumerate(raw_levels)]
    else:
        # Default: zweistufig. Root -> Projekt.
        levels = [Level(name="root"),
                  Level(name="project", is_work_unit=True)]

    # `max_depth` schaltet den Auto-Modus ein: das oberste markierte Verzeichnis
    # je Pfad. Noetig, sobald die Roots unterschiedlich tief sind — und das sind
    # sie fast immer (Spiele direkt unter der Wurzel, Software-Projekte eine
    # Kategorie-Ebene tiefer). Eine starre Ebenenzahl findet dann nur die eine
    # Sorte.
    raw_depth = section.get("max_depth")
    max_depth = int(raw_depth) if raw_depth is not None else None

    return TraversalConfig(
        roots=roots,
        levels=levels,
        skip_dirs=tuple(section.get("skip_dirs", DEFAULT_SKIP_DIRS)),
        max_depth=max_depth,
        markers=tuple(section.get("markers", []) or []),
        rules=marker_rules(),
    )


def marker_rules():
    """Baut die MarkerRules aus `[traversal.markers]`.

    Fehlt der Abschnitt ganz, wird None zurueckgegeben — dann gilt die einfache
    `markers`-Liste (rueckwaertskompatibel).
    """
    from .markers import (COMMON_DIR_PATTERNS, COMMON_MARKER_FILES,
                          COMMON_MARKER_SUBDIRS, DEFAULT_FLAG_FILE,
                          DirPatternRule, FileRule, FlagFileRule, GitRule,
                          MarkerRules, SubdirRule)

    section = (load_config().get("traversal", {}) or {}).get("markers")
    if not isinstance(section, dict):
        return None

    def sub(name: str) -> dict:
        value = section.get(name)
        return value if isinstance(value, dict) else {}

    dirs = sub("dir_patterns")
    files = sub("files")
    subdirs = sub("subdirs")
    git = sub("git")
    flag = sub("flag_file")

    return MarkerRules(
        dir_patterns=DirPatternRule(
            enabled=bool(dirs.get("enabled", False)),
            patterns=tuple(dirs.get("patterns", COMMON_DIR_PATTERNS)),
            require_all=bool(dirs.get("require_all", False)),
        ),
        files=FileRule(
            enabled=bool(files.get("enabled", True)),
            names=tuple(files.get("names", COMMON_MARKER_FILES)),
            require_all=bool(files.get("require_all", False)),
        ),
        subdirs=SubdirRule(
            enabled=bool(subdirs.get("enabled", True)),
            names=tuple(subdirs.get("names", COMMON_MARKER_SUBDIRS)),
            require_all=bool(subdirs.get("require_all", False)),
        ),
        git=GitRule(
            enabled=bool(git.get("enabled", True)),
            require_worktree_root=bool(git.get("require_worktree_root", False)),
        ),
        flag_file=FlagFileRule(
            enabled=bool(flag.get("enabled", True)),
            name=str(flag.get("name", DEFAULT_FLAG_FILE)),
        ),
        combine=str(section.get("combine", "any")).lower(),
        expression=str(section.get("expression", "")),
    )


def lock_config() -> Dict[str, Any]:
    """Lock-Provider und die Pfade fremder Regelwerke.

    `provider = "lockmaster"` -> bekanntes Schema, deterministisch ausgewertet.
    `provider = "rules"`      -> fremdes System: NICHT auswerten, sondern die
                                 hinterlegten Regeldateien als Text in den Prompt
                                 reichen. Lieber ein Agent, der die echte Regel
                                 liest, als ein Parser, der sie errät.
    """
    section = load_config().get("locks", {}) or {}
    return {
        "provider": str(section.get("provider", "lockmaster")),
        "rule_paths": [Path(p).expanduser()
                       for p in section.get("rule_paths", []) or []],
        "max_depth": int(section.get("max_depth", 4)),
    }


def active_roles() -> Dict[str, bool]:
    """Welche Rollen laufen? Abgeschaltete Rolle -> Starter bricht sauber ab."""
    section = load_config().get("roles", {}) or {}
    return {
        "taskwriter": bool(section.get("taskwriter", True)),
        "tasksolver": bool(section.get("tasksolver", True)),
        "maintainer": bool(section.get("maintainer", True)),
        "combined": bool(section.get("combined", False)),
    }


def provider_name(explicit: str = "") -> str:
    """Aktiver LLM-Provider, ohne Rechner- oder Benutzernamen im Code.

    Reihenfolge: expliziter Parameter > ENV ``TASKPLAN_PROVIDER`` >
    ``[execution] provider`` > leer. Ein leerer Provider erhaelt das bisherige
    Verhalten und liest die alte, providerlose ``[models]``-Sektion.
    """
    if explicit:
        return explicit.strip().lower()
    env = os.environ.get("TASKPLAN_PROVIDER", "").strip().lower()
    if env:
        return env
    section = load_config().get("execution", {}) or {}
    return str(section.get("provider", "")).strip().lower()


def _provider_section(provider: str = "") -> Dict[str, Any]:
    configured = load_config().get("providers", {}) or {}
    if not isinstance(configured, dict):
        return {}
    section = configured.get(provider_name(provider), {}) or {}
    return section if isinstance(section, dict) else {}


def _role_value(section: Dict[str, Any], key: str, role: str,
                default: str = "") -> str:
    values = section.get(key, {}) or {}
    if not isinstance(values, dict):
        return default
    return str(values.get(role) or values.get("default", default))


def model_for(role: str, provider: str = "") -> str:
    """Modell einer Rolle, bevorzugt aus der Provider-Konfiguration.

    ``[providers.<name>.models]`` ist der nutzerneutrale Weg. Die bisherige
    ``[models]``-Sektion bleibt als vollstaendig kompatibler Fallback erhalten.
    Damit koennen Claude, Codex oder weitere Provider eigene Modellnamen tragen,
    ohne dass ein Starter eine fremde Wahl fest verdrahtet.
    """
    resolved = provider_name(provider)
    if resolved:
        # Ein expliziter Provider darf NIE ein Modell eines anderen Providers
        # aus der alten globalen Sektion erben.
        return _role_value(_provider_section(resolved), "models", role)
    legacy = load_config().get("models", {}) or {}
    if not isinstance(legacy, dict):
        return ""
    return str(legacy.get(role) or legacy.get("default", ""))


def provider_runtime(role: str, provider: str = "") -> Dict[str, Any]:
    """Provider-spezifische Laufzeitdaten fuer einen duennen Starter."""
    resolved = provider_name(provider)
    section = _provider_section(resolved) if resolved else {}
    default_continuation = "goal" if resolved == "codex" else "one_shot"
    continuation = str(section.get("continuation", default_continuation)).lower()
    empty_policy = str(section.get(
        "empty_policy", "keep_goal" if continuation == "goal" else "stop"
    )).lower()
    return {
        "provider": resolved,
        "role": role.strip().lower(),
        "model": model_for(role, resolved),
        "reasoning_effort": _role_value(section, "reasoning_effort", role),
        "continuation": continuation,
        "empty_policy": empty_policy,
        "idle_backoff_seconds": max(0, int(section.get("idle_backoff_seconds", 60))),
    }


def discovery_timeout_seconds() -> float:
    """Harte Grenze fuer Projekt-Discovery; 0 schaltet sie bewusst aus."""
    section = load_config().get("traversal", {}) or {}
    try:
        return max(0.0, float(section.get("discovery_timeout_seconds", 30)))
    except (TypeError, ValueError):
        return 30.0


def discovery_cache_config() -> Dict[str, Any]:
    """Portabler Snapshot der teuren Projekt-Discovery.

    ``ttl_seconds`` ist das Refresh-Intervall, kein Löschdatum: Ein
    Last-known-good-Sektor bleibt bei einem fehlgeschlagenen Refresh nutzbar.
    """
    section = load_config().get("traversal", {}) or {}
    raw_path = str(section.get("cache_file", "~/.taskplan/projects-cache.json"))
    try:
        ttl = max(0, int(section.get("cache_ttl_seconds", 86400)))
    except (TypeError, ValueError):
        ttl = 86400
    return {
        "enabled": bool(section.get("cache_enabled", True)),
        "path": Path(raw_path).expanduser(),
        "ttl_seconds": ttl,
    }
