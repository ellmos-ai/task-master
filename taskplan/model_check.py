# -*- coding: utf-8 -*-
"""Pruefmodus `taskplan check-models` gegen veraltete und hartkodierte Modellnamen."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

try:
    import tomllib  # Python >= 3.11
except ImportError:  # pragma: no cover - nur auf Python 3.10
    tomllib = None  # type: ignore[assignment]

from .config import find_config_file

SCRIPT_EXTENSIONS = {".bat", ".cmd", ".sh", ".ps1"}
SKIP_DIR_NAMES = {"_archive", "_archiviert", "_archiv", ".git", "__pycache__", ".venv"}

DEPRECATED_MODEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deprecated_gemini_3x", re.compile(r"\bgemini-3(?:\.\d+)+[a-z0-9_-]*\b", re.IGNORECASE)),
    ("deprecated_flash_lite", re.compile(r"\bflash[-_]lite\b", re.IGNORECASE)),
    ("deprecated_gpt_5x", re.compile(r"\bgpt-5(?:\.\d+)+[a-z0-9_-]*\b", re.IGNORECASE)),
    (
        "deprecated_claude_4x",
        re.compile(
            r"\b(?:claude-(?:opus|sonnet|haiku)-4(?:[.-]\d+)*|(?:opus|sonnet)-4(?:[.-]\d+)+)\b",
            re.IGNORECASE,
        ),
    ),
)

_MODEL_FLAG_RE = re.compile(
    r'(?:--model(?:=|\s+))(["\']?)([^"\'\s]+)\1',
    re.IGNORECASE,
)
_SET_MODEL_RE = re.compile(
    r'^\s*(?:set\s+"?|(?:export\s+)?)MODEL\s*=\s*(["\']?)([^"\'\s]+)\1',
    re.IGNORECASE,
)


def _is_comment_line(line: str, suffix: str) -> bool:
    stripped = line.lstrip()
    if not stripped:
        return True
    if suffix in {".bat", ".cmd"}:
        upper = stripped.upper()
        return upper.startswith("REM ") or upper == "REM" or stripped.startswith("::")
    return stripped.startswith("#")


def _is_dynamic_or_placeholder(value: str) -> bool:
    return any(marker in value for marker in ("%", "!", "$", "<", ">"))


def find_deprecated_model_matches(text: str) -> list[tuple[str, str]]:
    """Liefert (Regelname, Treffer) fuer bekannte veraltete Modell-IDs."""
    matches: list[tuple[str, str]] = []
    for rule, pattern in DEPRECATED_MODEL_PATTERNS:
        for found in pattern.findall(text):
            matches.append((rule, found))
    return matches


def scan_starter_file(path: Path) -> list[dict[str, Any]]:
    """Prueft eine einzelne Starter-/Skriptdatei auf veraltete oder hartkodierte Modelle."""
    findings: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()

    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, hit in find_deprecated_model_matches(line):
            findings.append(
                {
                    "file": str(path),
                    "line": lineno,
                    "rule": rule,
                    "match": hit,
                    "detail": f"Veraltete Modell-ID {hit!r} gefunden.",
                }
            )
        if _is_comment_line(line, suffix):
            continue
        for match in _MODEL_FLAG_RE.finditer(line):
            value = match.group(2)
            if not _is_dynamic_or_placeholder(value):
                findings.append(
                    {
                        "file": str(path),
                        "line": lineno,
                        "rule": "hardcoded_model_flag",
                        "match": value,
                        "detail": (
                            f"Hartkodiertes --model {value!r}; ueber "
                            "`taskplan runtime` aus taskplan.toml lesen."
                        ),
                    }
                )
        set_match = _SET_MODEL_RE.match(line)
        if set_match:
            value = set_match.group(2)
            if value and not _is_dynamic_or_placeholder(value) and not value.startswith("%"):
                findings.append(
                    {
                        "file": str(path),
                        "line": lineno,
                        "rule": "hardcoded_model_default",
                        "match": value,
                        "detail": (
                            f"Hartkodiertes MODEL={value!r}; ueber "
                            "`taskplan runtime` aus taskplan.toml lesen."
                        ),
                    }
                )
    return findings


def _parse_models_toml_fallback(text: str) -> dict[str, dict[str, str]]:
    """Minimaler stdlib-Parser fuer [providers.<p>.models] / [models] auf Python 3.10."""
    sections: dict[str, dict[str, str]] = {}
    current = ""
    section_re = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")
    kv_re = re.compile(r'^\s*([A-Za-z0-9_.-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')\s*(?:#.*)?$')
    for line in text.splitlines():
        sec_m = section_re.match(line)
        if sec_m:
            current = sec_m.group(1).strip()
            continue
        if not current or (current != "models" and not (current.startswith("providers.") and current.endswith(".models"))):
            continue
        kv_m = kv_re.match(line)
        if kv_m:
            key = kv_m.group(1)
            val = kv_m.group(2) if kv_m.group(2) is not None else (kv_m.group(3) or "")
            sections.setdefault(current, {})[key] = val
    return sections


def scan_config_file(path: Path) -> list[dict[str, Any]]:
    """Prueft [providers.*.models] und [models] einer TOML-Konfiguration auf Altmodelle."""
    if not path.is_file():
        return []

    findings: list[dict[str, Any]] = []

    def _check_table(table: Any, section_name: str) -> None:
        if not isinstance(table, dict):
            return
        for role_key, raw_value in table.items():
            if not isinstance(raw_value, str) or not raw_value.strip():
                continue
            for rule, hit in find_deprecated_model_matches(raw_value):
                findings.append(
                    {
                        "file": str(path),
                        "line": 0,
                        "rule": rule,
                        "match": hit,
                        "detail": (
                            f"Veraltetes Modell {raw_value!r} in "
                            f"[{section_name}] {role_key}."
                        ),
                    }
                )

    if tomllib is not None:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
        providers = data.get("providers", {})
        if isinstance(providers, dict):
            for pname, psection in providers.items():
                if isinstance(psection, dict):
                    _check_table(psection.get("models"), f"providers.{pname}.models")
        _check_table(data.get("models"), "models")
        return findings

    parsed = _parse_models_toml_fallback(path.read_text(encoding="utf-8", errors="replace"))
    for section_name, table in parsed.items():
        _check_table(table, section_name)
    return findings


def _iter_starter_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for target in paths:
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            for candidate in sorted(target.rglob("*")):
                if not candidate.is_file():
                    continue
                if any(part in SKIP_DIR_NAMES for part in candidate.parts):
                    continue
                if candidate.suffix.lower() in SCRIPT_EXTENSIONS:
                    files.append(candidate)
    return files


def check_models(
    paths: Iterable[Path] | None = None,
    *,
    config_path: Path | None = None,
    include_config: bool = True,
) -> dict[str, Any]:
    """Prueft Starter-Dateien und optional taskplan.toml auf Modell-Altlasten."""
    if paths is None:
        target_paths = [Path(__file__).resolve().parent / "starters"]
    else:
        target_paths = list(paths)

    checked_files: list[str] = []
    findings: list[dict[str, Any]] = []

    for script_path in _iter_starter_files(target_paths):
        checked_files.append(str(script_path))
        findings.extend(scan_starter_file(script_path))

    resolved_cfg = config_path if config_path is not None else (
        find_config_file() if include_config else None
    )
    if resolved_cfg is not None and resolved_cfg.is_file():
        checked_files.append(str(resolved_cfg))
        findings.extend(scan_config_file(resolved_cfg))

    return {
        "ok": not findings,
        "checked_files": checked_files,
        "findings": findings,
    }
