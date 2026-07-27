# -*- coding: utf-8 -*-
"""Ebenen-Traversierung: Roots finden, Projekte erkennen.

Der Kern des Oberflaechen-Problems war eine fehlende Definition: "Pipeline" und
"Projekt" wurden im ganzen System nirgends unterschieden. Die Rotation kannte nur
eine flache Liste von 13 Wurzeln — eine Projektebene existierte gar nicht. Der
Loop setzte deshalb Pipeline = Projekt und ist nie hinabgestiegen.

Hier wird die Ebene strukturell definiert, nicht per Namensliste:

    Root     Einstiegspunkt der Traversierung (kommt aus der Umgebung)
    Level    eine Ebene darunter; genau eine traegt `is_work_unit`
    Projekt  ein Verzeichnis auf der Arbeitsebene, das einen MARKER traegt

Damit bleibt das Modul frei von den Namen UND von der Verzeichnistiefe einer
konkreten Installation. Wer zwei Ebenen braucht, laesst den Slot weg; wer vier
braucht, haengt eine an.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from .markers import MarkerRules

DEFAULT_MARKERS = ("TODO.md", "ROADMAP.md", "AUFGABEN.md", "AUFGABEN.txt",
                   ".git", "pyproject.toml", "package.json")

DEFAULT_SKIP_DIRS = ("_archive", "_archiviert", "node_modules", ".venv",
                     "__pycache__", ".git", "dist", "build")


@dataclass(frozen=True)
class Level:
    """Eine Ebene der Traversierung.

    `markers` leer = jedes Unterverzeichnis zaehlt (reine Gruppierungsebene,
    z. B. ein Slot). `is_work_unit` markiert die Ebene, auf der tatsaechlich
    gearbeitet und gelockt wird — genau eine Ebene traegt sie.
    """
    name: str
    markers: tuple[str, ...] = ()
    is_work_unit: bool = False

    def __post_init__(self):
        if isinstance(self.markers, list):
            object.__setattr__(self, "markers", tuple(self.markers))


@dataclass
class TraversalConfig:
    """Wie tief liegt ein Projekt?

    Zwei Betriebsarten — weil beide gebraucht werden:

    `levels`     STARRE Ebenenzahl. Ein Projekt liegt auf genau einer Ebene.
                 Praezise, aber alle Roots muessen gleich tief sein.

    `max_depth`  AUTO. Ein Projekt ist das OBERSTE markierte Verzeichnis auf
                 seinem Pfad, bis zu dieser Tiefe. Noetig, sobald die Roots
                 unterschiedlich tief sind — und das ist der Normalfall:
                 Spiele liegen direkt unter ihrer Wurzel, Software-Projekte
                 haengen eine Kategorie-Ebene tiefer.

    Ist `max_depth` gesetzt, gewinnt der Auto-Modus.

    Grenze des Auto-Modus, ehrlich benannt: Traegt eine ZWISCHENebene selbst
    einen Marker (ein `TODO.md` im Kategorie-Ordner), gilt sie als Projekt. Wer
    das ausschliessen muss, nimmt `levels` oder setzt den Ordner auf `skip_dirs`.
    """
    roots: List[Path] = field(default_factory=list)
    levels: List[Level] = field(default_factory=list)
    skip_dirs: tuple[str, ...] = DEFAULT_SKIP_DIRS
    max_depth: Optional[int] = None
    markers: tuple[str, ...] = ()
    # Die reichhaltige Variante: vier Kategorien (Ordnermuster, Dateien,
    # Subordner, Flagdatei), einzeln schaltbar und per any/all verknuepft.
    # Ist sie gesetzt, gewinnt sie ueber die einfache `markers`-Liste.
    rules: Optional["MarkerRules"] = None

    def __post_init__(self):
        self.roots = [Path(r) for r in self.roots]
        self.skip_dirs = tuple(self.skip_dirs)
        self.markers = tuple(self.markers)

    def is_project(self, directory: Path) -> bool:
        """Ist dieses Verzeichnis ein Projekt?"""
        if self.rules is not None:
            return self.rules.matches(directory)
        return _has_marker(directory, self.effective_markers())

    @property
    def work_level_index(self) -> int:
        """Index der Arbeitsebene. Ohne Markierung gilt die letzte Ebene."""
        for index, level in enumerate(self.levels):
            if level.is_work_unit:
                return index
        return len(self.levels) - 1

    def effective_markers(self) -> tuple[str, ...]:
        if self.markers:
            return self.markers
        if self.levels:
            work = self.levels[self.work_level_index]
            if work.markers:
                return work.markers
        return DEFAULT_MARKERS


@dataclass(frozen=True)
class Project:
    """Eine Arbeitseinheit — das, was Rotation, Lock-Scope und `project_path` meinen."""
    path: Path
    root_id: str

    @property
    def name(self) -> str:
        return self.path.name


def find_roots(lock_roots_json: Path) -> List[Path]:
    """Liest das Roots-Inventar aus `lock_roots.json`.

    Die Datei existiert bereits und wird vom Lock-Scanner gepflegt. Eine zweite
    Roots-Liste anzulegen wuerde unweigerlich auseinanderlaufen — deshalb wird
    diese hier wiederverwendet statt dupliziert.

    Ein Root ist entweder ein blosser Pfad oder ein Objekt `{"path": ...,
    "shallow": true}`. Die Pfade koennen Umgebungsvariablen enthalten
    (`%USERPROFILE%` auf Windows, `$HOME` auf Unix) — sie werden aufgeloest.
    Genau daran scheiterte der erste Versuch: 17 Roots, 0 gefunden, weil
    `%USERPROFILE%` als Verzeichnisname interpretiert wurde.

    Nicht (mehr) existierende Eintraege werden uebersprungen, nicht als Fehler
    behandelt: Ein umbenannter Ordner darf den ganzen Lauf nicht abbrechen.
    """
    path = Path(lock_roots_json)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    roots: List[Path] = []
    for entry in data.get("roots", []):
        raw = entry.get("path") if isinstance(entry, dict) else entry
        if not raw:
            continue
        candidate = Path(os.path.expandvars(str(raw))).expanduser()
        if candidate.is_dir():
            roots.append(candidate)
    return roots


def shallow_roots(lock_roots_json: Path) -> set[str]:
    """Roots, die als `shallow` markiert sind — riesige Baeume (z. B. Wissens-
    ablagen), die nur flach gescannt werden duerfen. Der Lock-Scanner kennt die
    Markierung bereits; wir respektieren sie, statt uns an OneDrive festzufahren.
    """
    path = Path(lock_roots_json)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    marked = set()
    for entry in data.get("roots", []):
        if isinstance(entry, dict) and entry.get("shallow"):
            raw = entry.get("path", "")
            resolved = Path(os.path.expandvars(str(raw))).expanduser()
            marked.add(resolved.name)
    return marked


def _has_marker(directory: Path, markers: Iterable[str]) -> bool:
    return any((directory / marker).exists() for marker in markers)


def _child_directories(parent: Path) -> List[Path]:
    """Günstige, fehlerisolierte Verzeichnisauflistung.

    Entspricht FileCommanders erfolgreichem ``readdir(..., withFileTypes)``-
    Muster: ein Verzeichnis-Handle, Typinformation aus dem Directory Entry und
    kein separater ``stat``-Lauf über jedes Kind. Cloud-Timeouts begrenzt der
    Discovery-Unterprozess weiterhin hart.
    """
    children: List[Path] = []
    try:
        with os.scandir(parent) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        children.append(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        return []
    return children


def _find_projects_auto(config: TraversalConfig,
                        roots: List[Path]) -> List[Project]:
    """Auto-Modus: das OBERSTE markierte Verzeichnis je Pfad, bis `max_depth`.

    Noetig, sobald die Roots unterschiedlich tief sind — der Normalfall. Eine
    starre Ebenenzahl findet dann entweder die flachen ODER die tiefen Projekte,
    nie beide.

    Sobald ein Verzeichnis als Projekt erkannt ist, wird NICHT weiter hinabgestiegen:
    Ein Unterordner eines Projekts ist Teil davon, kein eigenes Projekt.
    """
    max_depth = config.max_depth or 3
    projects: List[Project] = []

    for root in roots:
        if not root.is_dir():
            continue
        frontier = [root]
        for _ in range(max_depth):
            nxt: List[Path] = []
            for parent in frontier:
                for child in _child_directories(parent):
                    if child.name in config.skip_dirs:
                        continue
                    if config.is_project(child):
                        projects.append(Project(path=child, root_id=root.name))
                    else:
                        nxt.append(child)   # nur unmarkierte Zweige weiterverfolgen
            frontier = nxt
            if not frontier:
                break
    return projects


def discover_projects(config: TraversalConfig, mode: str = "hybrid",
                      registry_file: str = "",
                      only_root: Optional[Path] = None) -> List[Project]:
    """Findet Projekte gemaess dem Discovery-Modus.

    Die Automatik erkennt Projekte an MARKERN. Das setzt voraus, dass ein System
    diese Konventionen ueberhaupt benutzt — ein anderer Anwender hat vielleicht
    keine Steuerdateien, eine irrefuehrende Zwischenebene oder ganz andere
    Dateinamen. Fuer ihn gibt es die manuelle Registry.

    "auto"    nur Marker
    "manual"  nur Registry
    "hybrid"  beides, dedupliziert (Default) — die Automatik traegt, die
              Registry korrigiert. Eingetragene Projekte GEWINNEN bei Dubletten:
              Wer von Hand etwas eintraegt, hat einen Grund.
    """
    from .registry import registered_projects

    found: List[Project] = []
    if mode in ("auto", "hybrid"):
        found = find_projects(config, only_root=only_root)

    if mode in ("manual", "hybrid"):
        manual = registered_projects(registry_file)
        if only_root is not None:
            root = Path(only_root).resolve()
            manual = [p for p in manual
                      if p.path.resolve() == root or root in p.path.resolve().parents]
        # Registry gewinnt: eingetragene Pfade ersetzen automatisch gefundene.
        manual_paths = {p.path.resolve() for p in manual}
        found = [p for p in found if p.path.resolve() not in manual_paths]
        found.extend(manual)

    return found


def find_projects(config: TraversalConfig,
                  only_root: Optional[Path] = None) -> List[Project]:
    """Findet alle Arbeitseinheiten unterhalb der konfigurierten Roots (Automatik).

    Kein unbegrenzter Baumscan: Entweder die Ebenenliste gibt die Tiefe vor, oder
    `max_depth` begrenzt den Auto-Modus.
    """
    roots = [Path(only_root)] if only_root is not None else config.roots

    if config.max_depth is not None:
        return _find_projects_auto(config, roots)

    if not config.levels:
        return []

    work_index = config.work_level_index
    if work_index <= 0:
        return []  # Die Root selbst ist die Arbeitsebene -> keine Projekte darunter

    projects: List[Project] = []

    for root in roots:
        if not root.is_dir():
            continue
        # Ebene fuer Ebene absteigen — nicht rglob, sonst waere die Ebenenzahl
        # bedeutungslos und ein tief verschachteltes TODO.md wuerde faelschlich
        # als Projekt gelten.
        current = [root]
        for depth in range(1, work_index + 1):
            nxt: List[Path] = []
            for parent in current:
                for child in _child_directories(parent):
                    if child.name in config.skip_dirs:
                        continue
                    if depth == work_index:
                        if config.is_project(child):
                            nxt.append(child)
                    else:
                        nxt.append(child)
            current = nxt
        projects.extend(Project(path=p, root_id=root.name) for p in current)

    return projects
