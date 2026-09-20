# -*- coding: utf-8 -*-
"""Woran erkennt man ein Projekt? Fuenf Kategorien, einzeln und kombinierbar.

Ein einzelner Marker reicht nie fuer alle Systeme. `TODO.md` liegt auch in
Ordnern, die keine Projekte sind. Ein `.git` fehlt bei allem, was nie versioniert
wurde. Und manche Strukturen erkennt man ueberhaupt nur am Ordnernamen.

Deshalb fuenf Kategorien, jede einzeln abschaltbar:

    1. ORDNERMERKMALE   dir_patterns  Muster im Namen (Punkt am Anfang, Grossbuchstabe)
    2. MARKERDATEIEN    files         Dateien im Projekt-Root (CLAUDE.md, TODO.md, ...)
    3. SUBORDNER        subdirs       Verzeichnisse im Projekt-Root (.claude, ...)
    4. GIT              git           ein Repository (auch Worktree/Submodul)
    5. FLAGDATEI        flag_file     eine ausdrueckliche Markierung

Verknuepfen kann man sie auf zwei Weisen:

    combine = "any" | "all"     Kurzform.

    expression = "..."          Der volle Ausdruck: UND, ODER, NICHT, Klammern.

                                    (dir_patterns AND files) OR git
                                    files AND NOT subdirs
                                    git OR (dir_patterns AND flag_file)

                                Noetig, sobald "alles oder eines" nicht mehr
                                reicht — und das ist schnell der Fall.

Die FLAGDATEI hat Sonderstatus, solange sie NICHT im Ausdruck vorkommt: Liegt
sie da, ist es ein Projekt. Sie ist die ausdrueckliche Ansage des Nutzers, und
eine Ansage schlaegt jede Heuristik. Wer sie im Ausdruck erwaehnt, uebernimmt
selbst die Kontrolle darueber.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

# Punkt am Anfang (`.AI`, `.RESEARCH`) oder Grossbuchstabe am Anfang
# (`DEV_DiversityCook`, `REL-PUB_FinancialProof`) — verbreitete Projektkonventionen.
#
# Ein erster Versuch verlangte DURCHGAENGIGE Grossschreibung (`^[A-Z][A-Z0-9_.-]*$`)
# und fiel bei `DEV_DiversityCook` durch: Der Praefix ist gross, der Rest CamelCase.
# Ordnernamen sind ein SCHWACHES Signal — sie taugen als Bestandteil einer
# Kombination, selten allein.
COMMON_DIR_PATTERNS = (r"^\.", r"^[A-Z]")

# CLAUDE.md ist spezifischer als TODO.md: Ein TODO.md liegt in jedem zweiten
# Ordner, eine CLAUDE.md nur dort, wo jemand bewusst einen Agentenkontext
# angelegt hat.
COMMON_MARKER_FILES = ("CLAUDE.md", "AGENTS.md", "TODO.md", "ROADMAP.md",
                       "AUFGABEN.md", "AUFGABEN.txt", "pyproject.toml",
                       "package.json")

# .git steht NICHT hier — es ist eine eigene Kategorie (GitRule), weil es ein
# staerkeres Signal ist als "irgendein Subordner".
COMMON_MARKER_SUBDIRS = (".claude",)

DEFAULT_FLAG_FILE = ".taskplan-project"


@dataclass
class DirPatternRule:
    """Muster im ORDNERNAMEN.

    WARNUNG — empirisch belegt (2026-07-14), und die Intuition taeuscht hier:
    Ordnermuster sind mit `combine="any"` GEFAEHRLICH, wenn die Zwischenebenen
    derselben Konvention folgen wie die Projekte.

    Beispiel aus der Praxis: Eine Softwareablage hat Kategorien `CASH`, `DATA`,
    `CODING` — alle GROSS geschrieben, genau wie die Projekte darunter. Das
    Muster trifft die KATEGORIE, der Scan haelt dort an und steigt nie zu den
    echten Projekten ab. Ergebnis: 46 gefundene "Projekte" statt 91 — und die
    46 sind die falschen.

    Deshalb steht diese Kategorie standardmaessig auf `enabled = false`. Sie ist
    ein SCHWACHES Signal und taugt als Bestandteil einer Kombination
    (`dir_patterns AND files`), selten allein.
    """
    enabled: bool = False
    patterns: tuple[str, ...] = COMMON_DIR_PATTERNS
    require_all: bool = False   # muessen ALLE Muster passen, oder genuegt eines?

    def matches(self, directory: Path) -> bool:
        if not self.enabled or not self.patterns:
            return False
        name = directory.name
        hits = [bool(re.search(p, name)) for p in self.patterns]
        return all(hits) if self.require_all else any(hits)


@dataclass
class FileRule:
    enabled: bool = True
    names: tuple[str, ...] = COMMON_MARKER_FILES
    require_all: bool = False

    def matches(self, directory: Path) -> bool:
        if not self.enabled or not self.names:
            return False
        hits = [(directory / n).is_file() for n in self.names]
        return all(hits) if self.require_all else any(hits)


@dataclass
class SubdirRule:
    enabled: bool = True
    names: tuple[str, ...] = COMMON_MARKER_SUBDIRS
    require_all: bool = False

    def matches(self, directory: Path) -> bool:
        if not self.enabled or not self.names:
            return False
        hits = [(directory / n).is_dir() for n in self.names]
        return all(hits) if self.require_all else any(hits)


def is_git_worktree(directory: Path) -> bool:
    """Ist das ein git-WORKTREE (kein eigenstaendiges Projekt)?

    Ein Worktree ist ein zweiter Arbeitsbaum DESSELBEN Repositories. Er traegt
    dieselben Markerdateien wie der Hauptklon und wurde deshalb bisher als
    eigenes Projekt gezaehlt -- auf diesem System 151 Stueck, jeder so gross wie
    das Original (T-20260920-535056160).

    Erkennung ohne `git`-Aufruf: `.git` ist dort eine DATEI `gitdir: <pfad>`,
    und der Pfad eines Worktrees laeuft ueber `.git/worktrees/<name>`. Ein
    Submodul zeigt dagegen auf `.git/modules/<name>` und bleibt ein Projekt.
    """
    git = Path(directory) / ".git"
    try:
        if not git.is_file():
            return False
        head = git.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return False
    match = re.match(r"\s*gitdir:\s*(.+)", head)
    if not match:
        return False
    target = match.group(1).strip().replace("\\", "/").casefold()
    return "/worktrees/" in target or target.endswith("/worktrees")


@dataclass
class GitRule:
    """Ein Git-Repository — eigene Kategorie, weil es ein STARKES Signal ist.

    Wer `git init` gemacht hat, hat eine Grenze gezogen. Das ist mehr wert als
    irgendein Subordner, deshalb steht es nicht in der Subordner-Liste, sondern
    hier — und laesst sich im Ausdruck einzeln ansprechen (`git AND files`,
    `NOT git`).

    Tuecke, die eine reine Verzeichnispruefung verpasst: In einem WORKTREE oder
    SUBMODUL ist `.git` eine DATEI (sie zeigt auf das echte Repository), kein
    Verzeichnis. Beides zaehlt.
    """
    enabled: bool = True
    require_worktree_root: bool = False   # nur echte Repos, keine Worktrees/Submodule
    # Ein Worktree ist NIE ein eigenes Projekt -- unabhaengig davon, welche
    # Kategorie angeschlagen hat. Deshalb ein Veto, keine weitere Kategorie.
    exclude_worktrees: bool = True

    def matches(self, directory: Path) -> bool:
        if not self.enabled:
            return False
        git = Path(directory) / ".git"
        if git.is_dir():
            return True
        if git.is_file():
            # Worktree oder Submodul — ein Projekt ist es trotzdem.
            return not self.require_worktree_root
        return False


@dataclass
class FlagFileRule:
    """Die ausdrueckliche Markierung — Sonderstatus.

    Sie ist der letzte Fallback der Automatik: Wo keine Heuristik greift, legt man
    (oder der MAINTAINER) eine Datei ab und die Sache ist entschieden.

    Bewusst OPTIONAL: Manche Nutzer wollen keine fremden Dateien in ihren
    Projekten. Fuer sie bleibt der Handeintrag in der Registry.
    """
    enabled: bool = True
    name: str = DEFAULT_FLAG_FILE

    def matches(self, directory: Path) -> bool:
        return self.enabled and bool(self.name) and (directory / self.name).is_file()


class ExpressionError(ValueError):
    """Der Ausdruck ist kaputt — mit Angabe, wo."""


def _tokenize(expression: str) -> List[str]:
    tokens = re.findall(r"\(|\)|\w+", expression)
    if not tokens:
        raise ExpressionError("Leerer Ausdruck")
    return tokens


def evaluate_expression(expression: str, values: dict) -> bool:
    """Wertet einen booleschen Ausdruck ueber den Marker-Kategorien aus.

        (dir_patterns AND files) OR flag_file
        files AND NOT subdirs
        flag_file OR (dir_patterns AND (files OR subdirs))

    Erlaubt: die vier Kategorienamen, AND/OR/NOT (auch UND/ODER/NICHT),
    Klammern. Bewusst ein eigener Parser und KEIN `eval` — eine
    Konfigurationsdatei darf niemals beliebigen Code ausfuehren, auch nicht die
    eigene.

    Rangfolge wie ueblich: NOT > AND > OR.
    """
    aliases = {"UND": "AND", "ODER": "OR", "NICHT": "NOT",
               "AND": "AND", "OR": "OR", "NOT": "NOT"}
    tokens = _tokenize(expression)
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> str:
        nonlocal position
        token = tokens[position]
        position += 1
        return token

    def parse_or() -> bool:
        left = parse_and()
        while (token := peek()) and aliases.get(token.upper()) == "OR":
            take()
            right = parse_and()
            left = left or right
        return left

    def parse_and() -> bool:
        left = parse_not()
        while (token := peek()) and aliases.get(token.upper()) == "AND":
            take()
            right = parse_not()
            left = left and right
        return left

    def parse_not() -> bool:
        token = peek()
        if token and aliases.get(token.upper()) == "NOT":
            take()
            return not parse_not()
        return parse_atom()

    def parse_atom() -> bool:
        token = peek()
        if token is None:
            raise ExpressionError("Ausdruck endet unerwartet")
        if token == "(":
            take()
            value = parse_or()
            if peek() != ")":
                raise ExpressionError("Fehlende schliessende Klammer")
            take()
            return value
        take()
        if token not in values:
            raise ExpressionError(
                f"Unbekannter Marker {token!r}. Erlaubt: {', '.join(sorted(values))}")
        return values[token]

    result = parse_or()
    if position != len(tokens):
        raise ExpressionError(f"Unerwartetes Zeichen: {tokens[position]!r}")
    return result


@dataclass
class MarkerRules:
    """Alle vier Kategorien plus ihre Verknuepfung.

    Zwei Wege, sie zu verknuepfen:

    `combine`     Kurzform. "any" = ein Treffer genuegt, "all" = alle aktiven
                  Kategorien muessen treffen.

    `expression`  Der volle Ausdruck — UND, ODER, NICHT, Klammern:

                      (dir_patterns AND files) OR flag_file
                      files AND NOT subdirs

                  Ist er gesetzt, gewinnt er ueber `combine`. Er ist noetig,
                  sobald "alles oder eines" nicht mehr reicht — etwa: "Ein
                  Projekt ist ein GROSS geschriebener Ordner MIT CLAUDE.md,
                  ODER irgendetwas mit einer Flagdatei."

    Die FLAGDATEI hat trotzdem Sonderstatus, wenn sie NICHT im Ausdruck
    vorkommt: Liegt sie da, ist es ein Projekt. Wer sie im Ausdruck erwaehnt,
    uebernimmt selbst die Kontrolle darueber.
    """
    dir_patterns: DirPatternRule = field(default_factory=DirPatternRule)
    files: FileRule = field(default_factory=FileRule)
    subdirs: SubdirRule = field(default_factory=SubdirRule)
    git: GitRule = field(default_factory=GitRule)
    flag_file: FlagFileRule = field(default_factory=FlagFileRule)
    combine: str = "any"          # any | all  (Kurzform)
    expression: str = ""          # gewinnt, wenn gesetzt

    def active_rules(self) -> List:
        """Die aktivierten Kategorien — OHNE die Flagdatei (die steht ausserhalb)."""
        return [r for r in (self.dir_patterns, self.files, self.subdirs, self.git)
                if r.enabled]

    def _values(self, directory: Path) -> dict:
        return {
            "dir_patterns": self.dir_patterns.matches(directory),
            "files": self.files.matches(directory),
            "subdirs": self.subdirs.matches(directory),
            "git": self.git.matches(directory),
            "flag_file": self.flag_file.matches(directory),
        }

    def vetoed(self, directory: Path) -> bool:
        """Harte Ausschluesse -- greifen NACH der Flagdatei, VOR jeder Heuristik."""
        return self.git.exclude_worktrees and is_git_worktree(directory)

    def matches(self, directory: Path) -> bool:
        if self.expression:
            values = self._values(directory)
            # Sonderstatus nur, solange der Ausdruck die Flagdatei nicht selbst
            # in die Hand nimmt.
            if "flag_file" not in self.expression and values["flag_file"]:
                return True
            if self.vetoed(directory):
                return False
            return evaluate_expression(self.expression, values)

        # Kurzform: Die ausdrueckliche Ansage schlaegt jede Heuristik.
        if self.flag_file.matches(directory):
            return True
        if self.vetoed(directory):
            return False

        rules = self.active_rules()
        if not rules:
            return False

        hits = [rule.matches(directory) for rule in rules]
        return all(hits) if self.combine == "all" else any(hits)

    def describe(self) -> str:
        """Menschenlesbar — fuer `projects markers` und den MAINTAINER."""
        parts = []
        if self.dir_patterns.enabled:
            joiner = " UND " if self.dir_patterns.require_all else " ODER "
            parts.append("dir_patterns = " + joiner.join(self.dir_patterns.patterns))
        if self.files.enabled:
            joiner = " UND " if self.files.require_all else " ODER "
            parts.append("files = " + joiner.join(self.files.names))
        if self.subdirs.enabled:
            joiner = " UND " if self.subdirs.require_all else " ODER "
            parts.append("subdirs = " + joiner.join(self.subdirs.names))
        if self.git.enabled:
            parts.append("git = .git (Verzeichnis oder Datei/Worktree)")
        if self.git.exclude_worktrees:
            parts.append("ausgeschlossen: git-Worktrees (.git zeigt auf .git/worktrees/)")
        if self.flag_file.enabled:
            parts.append(f"flag_file = '{self.flag_file.name}'")
        if not parts:
            return "keine Marker aktiv"

        head = "\n  ".join(parts)
        if self.expression:
            return f"{head}\n\n  Verknuepfung: {self.expression}"
        verbinder = "AND" if self.combine == "all" else "OR"
        used = [n for n, r in (("dir_patterns", self.dir_patterns),
                               ("files", self.files),
                               ("subdirs", self.subdirs),
                               ("git", self.git)) if r.enabled]
        combined = f" {verbinder} ".join(used) if used else "-"
        if self.flag_file.enabled:
            combined = f"flag_file OR ({combined})" if used else "flag_file"
        return f"{head}\n\n  Verknuepfung: {combined}"


def set_flag(directory: Path, name: str = DEFAULT_FLAG_FILE,
             note: str = "") -> Path:
    """Legt die Flagdatei ab — markiert ein Verzeichnis ausdruecklich als Projekt."""
    directory = Path(directory)
    flag = directory / name
    body = ("# Von TASKPLAN als Projekt-Wurzel markiert.\n"
            "# Diese Datei sagt: Hier beginnt ein Projekt. Loeschen hebt das auf.\n")
    if note:
        body += f"#\n# {note}\n"
    flag.write_text(body, encoding="utf-8")
    return flag


def clear_flag(directory: Path, name: str = DEFAULT_FLAG_FILE) -> bool:
    """Entfernt die Flagdatei. Nur sie — sonst nichts."""
    flag = Path(directory) / name
    if flag.is_file():
        flag.unlink()
        return True
    return False
