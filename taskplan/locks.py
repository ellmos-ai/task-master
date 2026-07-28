# -*- coding: utf-8 -*-
"""Lock-Modell: drei Achsen statt eines Schalters — und fremde Systeme.

## Warum drei Achsen

Ein Lock war bisher faktisch ein Ja/Nein, und die geschriebene Regel lautete
sogar "Verzeichnis als deferred abhaken" — also eine Ganz-Pipeline-Abschaltung,
weil in EINEM Unterprojekt jemand arbeitete. Das ist zu grob:

    Lesen / Analysieren     immer erlaubt. Ein Lock schuetzt vor Aenderung,
                            nicht vor Kenntnisnahme.
    Neue Datei anlegen      in der Regel erlaubt (kollidiert nicht mit fremder
                            Arbeit an BESTEHENDEN Dateien).
    Bestehende Datei aendern nur ohne fremden Lock im Scope.

## Warum ein Provider

Nicht jede Installation benutzt unser Lock-System. Code kann fremde Lock-Systeme
nicht zuverlaessig parsen — ein LLM kann deren Regeln aber LESEN. Daraus folgen
zwei Provider:

    lockmaster   bekanntes Schema (LOCK*.txt + LOCK.permissions.json)
                 -> deterministisch ausgewertet, kein Ermessen
    rules        fremdes System: der Nutzer hinterlegt Pfade zu seinen
                 Lock-Regeln. Das Modul liest sie NICHT aus, sondern reicht sie
                 als Text in den Prompt durch — das LLM wendet sie an.
    none         kein Lock-System
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Wie lange gilt ein Lock ohne ausdrueckliches Ablaufdatum? Das Sicherheitsnetz
# des Systems (nicht der Normalfall — wer lockt, gibt selbst wieder frei).
DEFAULT_LOCK_TTL_HOURS = 24

# `LOCK.user*.txt` ist absolut: Nur der Nutzer entfernt ihn. Ein Agent fasst ihn
# nie an — auch nicht, wenn er nominell abgelaufen waere.
# Trifft `LOCK.txt`, `LOCK.desktop.txt`, `LOCK.user.zenodo.txt`, `LOCK.team.HOST.txt`
# — aber NICHT `LOCK.permissions.json` (endet nicht auf .txt) und nicht `LOCKDATEI.txt`.
USER_LOCK_PATTERN = re.compile(r"^LOCK\.user(\.|\.txt$)", re.IGNORECASE)
LOCK_FILE_PATTERN = re.compile(r"^LOCK(\..*)?\.txt$", re.IGNORECASE)

READ = "read"
CREATE = "create"
MODIFY = "modify"


@dataclass(frozen=True)
class Lock:
    path: Path            # Verzeichnis, das gesperrt ist
    file: Path            # die Lock-Datei selbst
    is_user_lock: bool
    expired: bool

    @property
    def active(self) -> bool:
        # Ein User-Lock verfaellt nicht. Punkt.
        return self.is_user_lock or not self.expired


@dataclass
class LockView:
    """Der Lock-Zustand eines Laufs — einmal erhoben, dann dem Selektor gereicht.

    `extra_rules` traegt den Regeltext fremder Lock-Systeme; er gehoert in den
    Prompt, nicht in eine Auswertung.
    """
    locks: List[Lock] = field(default_factory=list)
    permissions: Dict[Path, dict] = field(default_factory=dict)
    extra_rules: List[str] = field(default_factory=list)

    def locks_for(self, directory: Path,
                  include_expired: bool = False) -> List[Lock]:
        """Locks, die dieses Verzeichnis betreffen.

        Ein Lock in `.RESEARCH/.LAB/RH/` sperrt DIESES Projekt — nicht die
        Pipeline `.RESEARCH` und nicht ihre Geschwister. Ein Lock WEITER OBEN
        sperrt aber alles darunter mit.

        Normalerweise werden abgelaufene Locks ausgeblendet. Der autonome
        Selektor fragt sie ausdruecklich mit ab: Ein stale Lock braucht erst
        Klaerung und darf nicht immer wieder dasselbe blockierte Buendel
        anziehen, solange andere sichere Arbeit verfuegbar ist.
        """
        directory = Path(directory).resolve()
        hits = []
        for lock in self.locks:
            if not include_expired and not lock.active:
                continue
            locked = lock.path.resolve()
            if directory == locked or locked in directory.parents:
                hits.append(lock)
        return hits

    def allows(self, directory: Path, action: str) -> bool:
        """Darf `action` in `directory` ausgefuehrt werden?

        Die zentrale Regel dieses Moduls — und die, deren Fehlen den Loop
        ganze Pipelines hat ueberspringen lassen.
        """
        if action == READ:
            return True  # Analyse ist NIE gesperrt.

        active = self.locks_for(directory)
        if any(lock.is_user_lock for lock in active):
            return False  # User-Lock ist absolut, auch fuers Anlegen.

        decision = self._permission_for(directory, action)
        if decision == "deny":
            return False
        if decision == "allow":
            return True

        if action == CREATE:
            # Eine NEUE Datei kollidiert nicht mit fremder Arbeit an bestehenden.
            return True
        return not active  # MODIFY: nur ohne fremden Lock im Scope.

    def allows_selection(self, directory: Path) -> bool:
        """Darf ein autonomer Lauf dieses Projekt neu auswaehlen?

        Der TTL-Verfall erlaubt nach bewusster manueller Pruefung weiterhin
        `MODIFY`. Fuer die automatische Auswahl ist ein vorhandener stale Lock
        jedoch ein Konfliktsignal: Der Selektor nimmt einen anderen Kandidaten,
        bis der Lock owner-sicher geklaert oder entfernt wurde.
        """
        locks = self.locks_for(directory, include_expired=True)
        if any(lock.is_user_lock for lock in locks):
            return False

        decision = self._permission_for(directory, MODIFY)
        if decision == "deny":
            return False
        if decision == "allow":
            return True
        return not locks

    def _permission_for(self, directory: Path, action: str) -> Optional[str]:
        """Wertet `LOCK.permissions.json` aus. Praezedenz: deny > ask > allow."""
        directory = Path(directory).resolve()
        verdicts = []
        for scope, rules in self.permissions.items():
            scope = Path(scope).resolve()
            if not (directory == scope or scope in directory.parents):
                continue
            for verdict in ("deny", "ask", "allow"):
                entries = rules.get(verdict) or []
                if action in entries or "*" in entries:
                    verdicts.append(verdict)
        for verdict in ("deny", "ask", "allow"):   # deny gewinnt
            if verdict in verdicts:
                return "deny" if verdict == "ask" else verdict
        return None


def _is_expired(lock_file: Path, ttl_hours: int) -> bool:
    try:
        age_hours = (time.time() - lock_file.stat().st_mtime) / 3600
    except OSError:
        return True
    return age_hours > ttl_hours


def scan_lockmaster(roots: List[Path], max_depth: int = 4,
                    ttl_hours: int = DEFAULT_LOCK_TTL_HOURS) -> LockView:
    """Erhebt Locks nach dem bekannten Schema (LOCK*.txt + LOCK.permissions.json)."""
    view = LockView()
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for directory in _walk(root, max_depth):
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                if not entry.is_file():
                    continue
                if entry.name.lower() == "lock.permissions.json":
                    try:
                        view.permissions[directory] = json.loads(
                            entry.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        pass  # Eine kaputte Regeldatei darf den Lauf nicht kippen.
                elif LOCK_FILE_PATTERN.match(entry.name):
                    view.locks.append(Lock(
                        path=directory,
                        file=entry,
                        is_user_lock=bool(USER_LOCK_PATTERN.match(entry.name)),
                        expired=_is_expired(entry, ttl_hours),
                    ))
    return view


def load_rule_texts(rule_paths: List[Path]) -> List[str]:
    """Liest fremde Lock-Regeln als TEXT — bewusst ohne Interpretation.

    Ein fremdes Lock-System zu parsen hiesse, seine Semantik zu raten. Der Text
    geht stattdessen in den Prompt: Das LLM liest die Regeln und wendet sie an.
    Lieber ein Agent, der die echte Regel liest, als ein Parser, der sie errät.
    """
    texts = []
    for path in rule_paths:
        path = Path(path).expanduser()
        try:
            texts.append(f"--- {path} ---\n{path.read_text(encoding='utf-8')}")
        except OSError:
            continue
    return texts


class LazyLockView(LockView):
    """Prueft Locks BEDARFSGESTEUERT statt alles vorab zu scannen.

    Der Vollscan (`scan_lockmaster`) laeuft rekursiv durch jede Root. Ueber
    einen Cloud-Ordner mit 16 Roots dauert das Minuten — und ist verschwendet:
    Der Selektor fragt am Ende nur nach den paar Projekten, die ueberhaupt
    Aufgaben haben.

    Diese Variante geht stattdessen vom ANGEFRAGTEN Verzeichnis nach OBEN und
    sucht die Lock-Dateien auf dem Weg zur Wurzel. Das sind eine Handvoll
    Verzeichnis-Zugriffe pro Anfrage statt eines Baumscans — und es findet
    dieselben Locks, weil ein Lock immer entweder im Projekt selbst oder ueber
    ihm liegt.
    """

    def __init__(self, roots: Optional[List[Path]] = None,
                 ttl_hours: int = DEFAULT_LOCK_TTL_HOURS):
        super().__init__()
        self._roots = [Path(r).resolve() for r in (roots or [])]
        self._ttl = ttl_hours
        self._cache: Dict[Path, List[Lock]] = {}

    def _chain(self, directory: Path) -> List[Path]:
        """Das Verzeichnis und seine Eltern — hoechstens bis zu einer Root."""
        directory = Path(directory).resolve()
        chain = [directory]
        for parent in directory.parents:
            chain.append(parent)
            if parent in self._roots:
                break
        return chain

    def _locks_in(self, directory: Path) -> List[Lock]:
        if directory in self._cache:
            return self._cache[directory]
        found: List[Lock] = []
        try:
            entries = list(directory.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if not entry.is_file():
                continue
            if entry.name.lower() == "lock.permissions.json":
                try:
                    self.permissions[directory] = json.loads(
                        entry.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            elif LOCK_FILE_PATTERN.match(entry.name):
                found.append(Lock(
                    path=directory,
                    file=entry,
                    is_user_lock=bool(USER_LOCK_PATTERN.match(entry.name)),
                    expired=_is_expired(entry, self._ttl),
                ))
        self._cache[directory] = found
        return found

    def locks_for(self, directory: Path,
                  include_expired: bool = False) -> List[Lock]:
        hits: List[Lock] = []
        for step in self._chain(directory):
            hits.extend(
                lock for lock in self._locks_in(step)
                if include_expired or lock.active
            )
        return hits

    def allows(self, directory: Path, action: str) -> bool:
        if action == READ:
            return True
        # Die Kette einmal ablaufen — das fuellt zugleich `permissions`.
        self.locks_for(directory)
        return super().allows(directory, action)


def build_lock_view(provider: str,
                    roots: Optional[List[Path]] = None,
                    rule_paths: Optional[List[Path]] = None,
                    max_depth: int = 4,
                    eager: bool = False) -> LockView:
    """Baut den Lock-Zustand gemaess dem konfigurierten Provider.

    `eager=True` erzwingt den Vollscan (nuetzlich fuer eine Uebersicht). Im
    Regelbetrieb wird bedarfsgesteuert geprueft — sonst kostet ein einzelner
    Loop-Lauf Minuten in einem Cloud-Ordner.
    """
    if provider == "lockmaster":
        view = (scan_lockmaster(roots or [], max_depth=max_depth)
                if eager else LazyLockView(roots or []))
    else:
        # "rules": fremdes System -> nichts auswerten, nur durchreichen.
        # "none":  kein Lock-System.
        view = LockView()

    if rule_paths:
        view.extra_rules = load_rule_texts(rule_paths)
    return view


def _walk(root: Path, max_depth: int):
    """Verzeichnisse bis `max_depth` — kein unbegrenzter Baumscan."""
    yield root
    frontier = [root]
    for _ in range(max_depth):
        nxt = []
        for parent in frontier:
            try:
                children = [c for c in parent.iterdir() if c.is_dir()]
            except OSError:
                continue
            for child in children:
                if child.name in DEFAULT_SKIP:
                    continue
                yield child
                nxt.append(child)
        frontier = nxt


DEFAULT_SKIP = {"node_modules", ".venv", "__pycache__", ".git", "_archive"}
