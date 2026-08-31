# -*- coding: utf-8 -*-
"""Abbrechbare, sektorweise und Last-known-good-gecachte Projekt-Discovery."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from .config import (discovery_cache_config, discovery_mode, registry_file,
                     traversal_config)
from .registry import load_registry
from .traversal import Project, TraversalConfig, discover_projects, find_projects

CACHE_VERSION = 2


class DiscoveryConfigurationError(RuntimeError):
    """Auto-Discovery ist aktiviert, aber es gibt keinen konfigurierten Root."""


def validate_discovery_configuration(
    config: TraversalConfig, mode: str
) -> None:
    """Verhindert, dass ein fehlendes Root-Inventar als gesunder Leerlauf gilt."""
    if mode in ("auto", "hybrid") and not config.roots:
        raise DiscoveryConfigurationError(
            "Projekt-Discovery ist auf "
            f"{mode!r} gestellt, aber es sind keine Traversal-Roots konfiguriert. "
            "Setze [traversal].roots_file oder [traversal].roots in taskplan.toml."
        )


@dataclass
class DiscoveryResult:
    projects: list[Project]
    source: str
    degraded: bool = False
    cache_age_seconds: float = 0.0
    refreshed_sector: str = ""
    pending_sectors: int = 0
    warnings: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "cached": self.source in {
                "fresh_cache", "stale_cache", "partial_cache",
            },
            "source": self.source,
            "degraded": self.degraded,
            "cache_age_seconds": round(self.cache_age_seconds, 3),
            "refreshed_sector": self.refreshed_sector,
            "pending_sectors": self.pending_sectors,
            "warnings": self.warnings,
            "projects": [
                {"path": str(project.path), "root_id": project.root_id}
                for project in self.projects
            ],
        }


def _path_key(path: str | Path) -> str:
    """Lexikalischer Pfadschlüssel ohne Cloud-I/O wie ``resolve()``/``stat()``."""
    return os.path.normcase(os.path.abspath(os.path.expandvars(
        os.path.expanduser(str(path))
    )))


def _is_within(path: str | Path, root: str | Path) -> bool:
    try:
        return os.path.commonpath([_path_key(path), _path_key(root)]) == _path_key(root)
    except (OSError, ValueError):
        return False


def _signature(config: TraversalConfig, mode: str, registry: str = "") -> str:
    """Signatur nur aus Discovery-relevanter Policy.

    Frühere Fassungen hashten den mtime der *gesamten* taskplan.toml. Dadurch
    invalidierte selbst ein Modellwechsel das Projektinventar. Roots und
    Registry-Inhalte werden sektorweise bzw. live behandelt und gehören
    ebenfalls nicht in diese Policy-Signatur.
    """
    rules = config.rules.describe() if config.rules is not None else ""
    payload = {
        "levels": [(level.name, level.markers, level.is_work_unit)
                   for level in config.levels],
        "skip_dirs": config.skip_dirs,
        "max_depth": config.max_depth,
        "markers": config.markers,
        "rules": rules,
        "mode": mode,
        "registry_path": _path_key(registry) if registry else "",
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=list
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _empty_cache(signature: str) -> dict[str, Any]:
    return {
        "version": CACHE_VERSION,
        "signature": signature,
        "updated_at": time.time(),
        "sectors": [],
    }


def _current_roots(config: TraversalConfig) -> dict[str, Path]:
    return {_path_key(root): Path(root) for root in config.roots}


def _migrate_legacy_cache(
    data: dict[str, Any],
    config: TraversalConfig,
    signature: str,
    ttl: int,
) -> dict[str, Any] | None:
    """V1-Snapshot konservativ in Root-Sektoren überführen.

    Die alte Signatur ist nach einer fachfremden Config-Änderung nicht mehr
    beweisbar. Eine junge V1-Datei wird trotzdem übernommen, wenn jeder Pfad
    rein lexikalisch unter einer aktuell konfigurierten Root liegt. Kein
    ``resolve()`` und kein ``is_dir()`` darf dabei OneDrive blockieren.
    """
    if data.get("version") != 1:
        return None
    created = float(data.get("created_at", 0) or 0)
    if not created:
        return None
    roots = _current_roots(config)
    sectors: dict[str, dict[str, Any]] = {}
    for root_key, root in roots.items():
        sectors[root_key] = {
            "root_path": str(root),
            "root_id": root.name,
            "refreshed_at": created,
            "attempted_at": 0.0,
            "projects": [],
        }

    for raw in data.get("projects", []):
        if not isinstance(raw, dict) or not raw.get("path"):
            continue
        matches = [
            (root_key, root)
            for root_key, root in roots.items()
            if _is_within(str(raw["path"]), root)
        ]
        if not matches:
            continue
        root_key, root = max(matches, key=lambda item: len(item[0]))
        sectors[root_key]["projects"].append({
            "path": str(raw["path"]),
            "root_id": str(raw.get("root_id") or root.name),
        })

    migrated = _empty_cache(signature)
    migrated["sectors"] = list(sectors.values())
    migrated["migrated_from_version"] = 1
    return migrated


def _load_cache(
    path: Path,
    config: TraversalConfig,
    signature: str,
    ttl: int,
) -> tuple[dict[str, Any], bool, bool]:
    data = _read_json(path)
    migrated = False
    policy_changed = False
    if data and data.get("version") == CACHE_VERSION:
        if data.get("signature") != signature:
            # Eine Policy-Änderung macht das bekannte Inventar refresh-fällig,
            # aber nicht plötzlich wertlos. Jeder Sektor bleibt LKG, bis er
            # erfolgreich nach der neuen Policy ersetzt wurde.
            for sector in data.get("sectors", []):
                if isinstance(sector, dict):
                    sector["refresh_required"] = True
            data["previous_signature"] = data.get("signature", "")
            data["signature"] = signature
            policy_changed = True
        cache = data
    elif data:
        cache = _migrate_legacy_cache(data, config, signature, ttl)
        if cache is None:
            return _empty_cache(signature), False, False
        migrated = True
    else:
        return _empty_cache(signature), False, False

    current = _current_roots(config)
    cache["sectors"] = [
        sector for sector in cache.get("sectors", [])
        if isinstance(sector, dict)
        and _path_key(sector.get("root_path", "")) in current
    ]
    return cache, migrated, policy_changed


def _sector_map(cache: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _path_key(sector.get("root_path", "")): sector
        for sector in cache.get("sectors", [])
        if isinstance(sector, dict) and sector.get("root_path")
    }


def _projects_from_sectors(cache: dict[str, Any]) -> list[Project]:
    projects: list[Project] = []
    seen: set[str] = set()
    for sector in cache.get("sectors", []):
        for raw in sector.get("projects", []):
            if not isinstance(raw, dict) or not raw.get("path"):
                continue
            key = _path_key(str(raw["path"]))
            if key in seen:
                continue
            seen.add(key)
            projects.append(Project(
                path=Path(str(raw["path"])),
                root_id=str(raw.get("root_id") or sector.get("root_id", "")),
            ))
    return projects


def _manual_projects(configured: str, config: TraversalConfig) -> list[Project]:
    """Registry ohne Cloud-Stat lesen; die Registry selbst ist die Autorität."""
    roots = list(config.roots)
    projects: list[Project] = []
    for entry in load_registry(configured):
        path = Path(os.path.expandvars(entry.path)).expanduser()
        if roots and not any(_is_within(path, root) for root in roots):
            continue
        projects.append(Project(path=path, root_id=entry.root_id))
    return projects


def _merge_projects(
    automatic: Iterable[Project],
    manual: Iterable[Project],
) -> list[Project]:
    """Registry gewinnt bei Pfad-Dubletten, ohne Cloud-Pfade aufzulösen."""
    merged = {_path_key(project.path): project for project in automatic}
    for project in manual:
        merged[_path_key(project.path)] = project
    return list(merged.values())


def _cache_age(cache: dict[str, Any]) -> float:
    refreshed = [
        float(sector.get("refreshed_at", 0) or 0)
        for sector in cache.get("sectors", [])
        if float(sector.get("refreshed_at", 0) or 0) > 0
    ]
    return max(0.0, time.time() - min(refreshed)) if refreshed else 0.0


def _due_sectors(
    cache: dict[str, Any],
    config: TraversalConfig,
    ttl: int,
    force: bool,
) -> list[tuple[Path, dict[str, Any]]]:
    sectors = _sector_map(cache)
    due: list[tuple[Path, dict[str, Any]]] = []
    now = time.time()
    for root in config.roots:
        key = _path_key(root)
        sector = sectors.get(key)
        if sector is None:
            sector = {
                "root_path": str(root),
                "root_id": Path(root).name,
                "refreshed_at": 0.0,
                "attempted_at": 0.0,
                "projects": [],
            }
            cache.setdefault("sectors", []).append(sector)
        age = now - float(sector.get("refreshed_at", 0) or 0)
        if (
            force
            or sector.get("refresh_required")
            or not sector.get("refreshed_at")
            or (ttl and age > ttl)
        ):
            due.append((Path(root), sector))
    # Ein hängender Root kommt nach einem Timeout ans Ende statt jeden Lauf zu
    # blockieren; fehlende Sektoren bleiben dennoch vor bereits alten.
    due.sort(key=lambda item: (
        bool(item[1].get("refreshed_at")),
        float(item[1].get("attempted_at", 0) or 0),
        float(item[1].get("refreshed_at", 0) or 0),
    ))
    return due


def discover_snapshot(force: bool = False) -> DiscoveryResult:
    config = traversal_config()
    mode = discovery_mode()
    validate_discovery_configuration(config, mode)
    registry = registry_file()
    cache_cfg = discovery_cache_config()
    signature = _signature(config, mode, registry)
    if not cache_cfg["enabled"]:
        projects = discover_projects(config, mode, registry)
        return DiscoveryResult(projects=projects, source="live_scan")
    cache, migrated, policy_changed = _load_cache(
        cache_cfg["path"], config, signature, cache_cfg["ttl_seconds"]
    )

    manual = (
        _manual_projects(registry, config)
        if mode in ("manual", "hybrid") else []
    )
    if mode == "manual":
        return DiscoveryResult(
            projects=manual,
            source="manual_registry",
            degraded=False,
        )

    due = _due_sectors(
        cache, config, cache_cfg["ttl_seconds"], force
    )
    refreshed_sector = ""
    if due:
        # Pro Prozess genau EIN Root. Der Cache bleibt währenddessen
        # Last-known-good; ein Parent-Timeout kann ihn sofort weiterverwenden.
        root, sector = due[0]
        sector["attempted_at"] = time.time()
        cache["updated_at"] = time.time()
        if cache_cfg["enabled"]:
            _atomic_write(cache_cfg["path"], cache)

        projects = find_projects(config, only_root=root)
        sector["projects"] = [
            {"path": str(project.path), "root_id": project.root_id}
            for project in projects
        ]
        sector["refreshed_at"] = time.time()
        sector.pop("refresh_required", None)
        cache["updated_at"] = time.time()
        refreshed_sector = str(root)
        if cache_cfg["enabled"]:
            _atomic_write(cache_cfg["path"], cache)
        due = _due_sectors(cache, config, cache_cfg["ttl_seconds"], False)
    elif migrated and cache_cfg["enabled"]:
        _atomic_write(cache_cfg["path"], cache)

    automatic = _projects_from_sectors(cache)
    projects = _merge_projects(automatic, manual)
    missing_or_due = len(due)
    if refreshed_sector:
        source = "sector_refresh" if not missing_or_due else "partial_cache"
    else:
        source = "fresh_cache"
    warnings = []
    if migrated:
        warnings.append("Legacy-Cache v1 wurde ohne Cloud-I/O in Root-Sektoren migriert.")
    if policy_changed:
        warnings.append(
            "Discovery-Policy geändert; Last-known-good-Sektoren werden "
            "schrittweise ersetzt."
        )
    if missing_or_due:
        warnings.append(
            f"{missing_or_due} Projektsektor(en) warten noch auf Aktualisierung."
        )
    return DiscoveryResult(
        projects=projects,
        source=source,
        degraded=bool(missing_or_due),
        cache_age_seconds=_cache_age(cache),
        refreshed_sector=refreshed_sector,
        pending_sectors=missing_or_due,
        warnings=warnings,
    )


def discover_cached(force: bool = False) -> tuple[list[Project], bool]:
    """Kompatible API: Liste plus Angabe, ob kein Sektor gescannt wurde."""
    result = discover_snapshot(force=force)
    return result.projects, result.source == "fresh_cache"


def read_last_known_good() -> DiscoveryResult | None:
    """Cache ohne Traversierung/``stat`` lesen, auch wenn er refresh-fällig ist."""
    config = traversal_config()
    mode = discovery_mode()
    validate_discovery_configuration(config, mode)
    registry = registry_file()
    cache_cfg = discovery_cache_config()
    signature = _signature(config, mode, registry)
    cache, migrated, policy_changed = _load_cache(
        cache_cfg["path"], config, signature, cache_cfg["ttl_seconds"]
    )
    automatic = _projects_from_sectors(cache)
    manual = (
        _manual_projects(registry, config)
        if mode in ("manual", "hybrid") else []
    )
    projects = _merge_projects(automatic, manual)
    if not projects:
        return None
    warnings = ["Projekt-Refresh fehlgeschlagen; Last-known-good-Inventar wird verwendet."]
    if migrated:
        warnings.append("Legacy-Cache v1 wurde als Last-known-good übernommen.")
    if policy_changed or any(
        bool(sector.get("refresh_required"))
        for sector in cache.get("sectors", [])
        if isinstance(sector, dict)
    ):
        warnings.append(
            "Discovery-Policy geändert; bisheriges Inventar bleibt bis zum "
            "erfolgreichen Sektor-Refresh aktiv."
        )
    return DiscoveryResult(
        projects=projects,
        source="stale_cache",
        degraded=True,
        cache_age_seconds=_cache_age(cache),
        pending_sectors=len(_due_sectors(
            cache, config, cache_cfg["ttl_seconds"], False
        )),
        warnings=warnings,
    )


def projects_from_task_store(store) -> DiscoveryResult | None:
    """Letzter lokaler Fallback: bekannte Projektpfade aus der Task-Datenbank."""
    config = traversal_config()
    validate_discovery_configuration(config, discovery_mode())
    roots = list(config.roots)
    projects: dict[str, Project] = {}
    for task in store.list(limit=None, include_done=True):
        raw_path = task.get("project_path")
        if not raw_path:
            continue
        matches = [root for root in roots if _is_within(raw_path, root)]
        if roots and not matches:
            continue
        root = max(matches, key=lambda item: len(_path_key(item))) if matches else None
        root_id = str(task.get("root_id") or (root.name if root else ""))
        projects[_path_key(raw_path)] = Project(Path(raw_path), root_id)
    if not projects:
        return None
    return DiscoveryResult(
        projects=list(projects.values()),
        source="task_store",
        degraded=True,
        warnings=[
            "Projektinventar wurde aus bekannten Task-Pfaden abgeleitet; "
            "Auto-Discovery und Cache waren nicht verfügbar."
        ],
    )


def fallback_after_failure(store) -> DiscoveryResult | None:
    """Geordnete lokale Fallback-Kette ohne erneuten Cloud-Scan."""
    return read_last_known_good() or projects_from_task_store(store)


def payload(force: bool = False) -> dict[str, Any]:
    return discover_snapshot(force=force).payload()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    print(json.dumps(payload(force="--force" in args), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
