# -*- coding: utf-8 -*-
"""Der Selektor: ein Zustandsautomat, kein Prompt-Absatz.

## Warum das hier Code ist und keine Prosa

Die Reihenfolge "erst die Oberflaeche, dann in die Tiefe; erst alle leichten,
dann die mittleren" stand bisher nur als Text im Prompt. Als Text ist sie eine
BITTE, die das Modell in jedem Durchlauf neu auslegt — und genau deshalb ist der
Loop nie in die Tiefe eskaliert, sondern leergelaufen ("0 neue Aufgaben", waehrend
ueber 250 Steuerdateien unterhalb der Wurzeln unangetastet lagen).

Arbeitsteilung:
    Selektor (hier)  WAS ist als naechstes dran — deterministisch, testbar
    LLM (Prompt)     URTEIL — ist das leicht? sicher? bestanden?

## Die Reihenfolge (Nutzervorgabe 2026-07-13)

`effort` ist die PRIMAERE Sortierdimension, die Root-Rotation nur die sekundaere:

    Oberflaechen-Sweep (alle Roots)
      -> Deep-Dive EASY in Root A
      -> zurueck an die Oberflaeche
      -> Deep-Dive EASY in Root B
      -> ... bis KEINE Root mehr easy hat
      -> ERST JETZT: der medium-Durchgang

Begruendung des Nutzers: Leichte Aufgaben entlasten genau die, die tief in einem
Spezialthema stecken. Eine liegengebliebene Kleinigkeit in Projekt A abzuraeumen
ist wertvoller, als in Projekt B in die Tiefe zu gehen. Deshalb existiert die
Unterscheidung easy/harder ueberhaupt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Protocol

from .locks import LockView

SURFACE = "surface"
DEEP = "deep"

# Autonom loesbar sind nur diese. `large`/`special` und alles mit scope=central
# bleiben dem Nutzer vorbehalten — das Gate sitzt hier, nicht im Prompt.
AUTONOMOUS_EFFORTS = ("easy", "medium")


class TaskStore(Protocol):
    """Das Protokoll, gegen das der Selektor arbeitet. Er kennt kein SQL.

    Damit ist der Zustandsautomat unabhaengig davon, WO die Aufgaben liegen —
    und gegen einen In-Memory-Store testbar.
    """

    def list(self, **kwargs) -> list: ...

    def get(self, task_id: int) -> Optional[dict]: ...


@dataclass
class SelectorConfig:
    deep_enabled: bool = True
    effort_ceiling: str = "medium"          # easy | medium
    easy_first_globally: bool = True        # easy ueber ALLE Roots vor dem ersten medium
    projects_per_dive: int = 1
    max_bundle_size: int = 3
    # Direkte Konstruktionen (Bibliothek/Bestandstests) behalten den alten
    # rein cursorbasierten Vertrag. Die geladene Konfiguration aktiviert den
    # persistenten Pool standardmäßig explizit.
    review_pool_enabled: bool = False
    # Nur der TASKWRITER braucht sie: Ist alles eingestuft, sucht er das
    # naechste Projekt, das noch GAR KEINE Aufgaben hat. Ohne diese Liste
    # findet er es nicht — und haette wieder nichts zu tun.
    projects: List = field(default_factory=list)

    def allowed_efforts(self) -> tuple[str, ...]:
        ceiling = self.effort_ceiling if self.effort_ceiling in AUTONOMOUS_EFFORTS else "easy"
        return AUTONOMOUS_EFFORTS[:AUTONOMOUS_EFFORTS.index(ceiling) + 1]


@dataclass
class Bundle:
    """Was der Loop in diesem Durchlauf tut."""
    mode: str                     # surface | deep
    effort: str                   # easy | medium
    root_id: str
    project_path: str
    tasks: List[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.tasks)


def _is_selectable(task: dict, allowed_efforts: tuple[str, ...]) -> bool:
    """Darf der Solver das autonom anfassen?

    Unklassifizierte Aufgaben (`effort` leer) werden NICHT als leicht behandelt.
    Lieber liegen lassen als faelschlich fuer harmlos halten — der TASKWRITER
    klassifiziert sie nach.
    """
    if task.get("scope", "local") == "central":
        return False
    return task.get("effort", "") in allowed_efforts


def _reachable(task: dict, locks: LockView) -> bool:
    """Ist das Projekt der Aufgabe ueberhaupt beschreibbar?

    Gesperrte Projekte werden herausgefiltert, BEVOR das LLM sie sieht — ein
    Lock in einem Projekt sperrt aber nur DIESES, nicht seine Nachbarn und nicht
    die ganze Pipeline. Genau das war der alte Fehler.
    """
    project = task.get("project_path") or ""
    if not project:
        return True   # Ohne Projektbezug (Root-Aufgabe) greift kein Projekt-Lock.
    return locks.allows_selection(Path(project))


def _dependency_ids(task: dict) -> Optional[tuple[int, ...]]:
    """Liest `depends-on=1,2` aus dem bestehenden Semikolon-Tagformat.

    `()` bedeutet: keine Abhaengigkeit deklariert. `None` bedeutet: Ein
    `depends-on`-Tag ist vorhanden, aber leer oder ungueltig. Ein kaputter
    Abhaengigkeitsvertrag darf nicht versehentlich als Freigabe gelten.
    """
    dependencies = []
    declared = False
    for tag in str(task.get("tags") or "").split(";"):
        key, separator, value = tag.partition("=")
        if not separator or key.strip().lower() != "depends-on":
            continue
        declared = True
        raw_ids = [raw_id.strip() for raw_id in value.split(",")]
        if not raw_ids or any(not raw_id for raw_id in raw_ids):
            return None
        for raw_id in raw_ids:
            try:
                dependency_id = int(raw_id)
            except ValueError:
                return None
            if dependency_id <= 0:
                return None
            if dependency_id not in dependencies:
                dependencies.append(dependency_id)
    if not declared:
        return ()
    return tuple(dependencies)


def _dependencies_satisfied(task: dict, store: TaskStore) -> bool:
    """Nur vollstaendig erledigte Vorstufen geben einen Solver-Task frei."""
    dependency_ids = _dependency_ids(task)
    if dependency_ids is None:
        return False
    for dependency_id in dependency_ids:
        dependency = store.get(dependency_id)
        if dependency is None or dependency.get("status") != "done":
            return False
    return True


def _candidates(store: TaskStore, effort: str, locks: LockView,
                surface: bool) -> List[dict]:
    """Offene, erreichbare Aufgaben eines Aufwandsgrads.

    `surface=True`  -> Aufgaben OHNE project_path (Root-/Wurzelaufgaben)
    `surface=False` -> Aufgaben MIT project_path (in den Projekten)
    """
    tasks = store.list(status="open", effort=effort, limit=500)
    out = []
    for task in tasks:
        if task.get("scope", "local") == "central":
            continue
        has_project = bool(task.get("project_path"))
        if surface == has_project:
            continue
        if not _reachable(task, locks):
            continue
        if not _dependencies_satisfied(task, store):
            continue
        out.append(task)
    return out


def _project_key(raw) -> str:
    """Normalisiert einen Projektpfad fuer die Cursor-Rotation."""
    try:
        return Path(raw).as_posix().rstrip("/").lower()
    except (TypeError, ValueError):
        return str(raw).lower()


def _rotate_solver_candidates(tasks: List[dict], after_project: str) -> List[dict]:
    """Dreht Solver-Kandidaten nach einem expliziten Skip-Cursor.

    Der TASKSOLVER arbeitet normalerweise das vom Selektor gelieferte
    Aufgabenbuendel ab. Muss ein Projekt wegen eines belegten, veralteten oder
    nicht zulaessigen Arbeitsstands uebersprungen werden, darf es den
    persistenten Projekt-Cursor wie die projektbasierten Rollen nutzen. Die
    Reihenfolge innerhalb eines Projekts bleibt stabil; nur die Projektgruppen
    werden hinter ``after_project`` neu angeordnet.
    """
    if not after_project or not tasks:
        return tasks

    keys = []
    for task in tasks:
        key = _project_key(task.get("project_path") or "")
        if key and key not in keys:
            keys.append(key)

    after_key = _project_key(after_project)
    if after_key not in keys or len(keys) < 2:
        return tasks

    start = (keys.index(after_key) + 1) % len(keys)
    order = keys[start:] + keys[:start]
    rank = {key: index for index, key in enumerate(order)}
    return sorted(
        tasks,
        key=lambda task: rank.get(
            _project_key(task.get("project_path") or ""), len(order)
        ),
    )


def _apply_task_revolver(tasks: List[dict],
                         deferred_ids: Optional[List[int]]) -> List[dict]:
    """Reiht zurueckgestellte Aufgaben ans Ende ALLER Kandidaten.

    Der Projekt-Cursor ist zu grob, wenn genau EINE Aufgabe blockiert ist:
    Das Projekt kommt im naechsten Rotationszyklus wieder, und dieselbe
    Aufgabe steht erneut vorn (``ORDER BY priority, updated_at, created_at``
    ist stabil). Der einzige verbleibende Hebel waere, ihre Etiketten zu
    verbiegen — ``effort`` hochstufen, ``priority`` senken, ``status``
    faelschen. Das waere Datenverfaelschung: Diese Felder beschreiben
    Eigenschaften der Aufgabe, nicht ihre Position in der Warteschlange.

    Deshalb dieser Revolver. Solange es frische Kandidaten gibt, werden
    NUR sie geliefert — eine zurueckgestellte Aufgabe soll auch nicht ueber
    die Projektbuendelung wieder hereinrutschen.

    Ist auf dieser Aufwandsstufe ALLES zurueckgestellt, gibt die Funktion
    eine LEERE Liste zurueck. Der Aufrufer geht damit zur naechsthoeheren
    Stufe weiter (``easy_first_globally``) beziehungsweise am Ende in den
    ehrlichen Leerlauf.

    Diese Zeile war zuerst anders gebaut — sie lieferte die am laengsten
    wartende Aufgabe zurueck, als Schutz gegen Verhungern. Der Live-Lauf
    2026-08-24 02:35 widerlegte das: Von 10 offenen ``easy``-Aufgaben
    trugen 7 ``scope=central`` (nie autonom), die uebrigen 3 waren
    zurueckgestellt. Der Fallback lieferte prompt eine davon erneut — und
    verhinderte damit, dass der Selektor ueberhaupt zu ``medium``
    weiterging. Bei 1497 offenen Aufgaben liess der Verhungerungsschutz
    also alles andere verhungern.

    Verhungern droht ohne ihn nicht: Die Trommel waechst nur durch
    ausdrueckliches ``skip --task``, eine zurueckgestellte Aufgabe kehrt
    zurueck, sobald irgendein frischer Kandidat auftaucht, und ``--undo``
    ist der bewusste Hebel.
    """
    if not deferred_ids or not tasks:
        return tasks

    rank = {}
    for index, raw in enumerate(deferred_ids):
        try:
            rank[int(raw)] = index
        except (TypeError, ValueError):
            continue
    if not rank:
        return tasks

    # Leere Liste, wenn alles zurueckgestellt ist: Der Aufrufer eskaliert
    # dann zur naechsten Aufwandsstufe statt eine blockierte Aufgabe erneut
    # auszuliefern (siehe Docstring, Live-Befund 2026-08-24).
    return [t for t in tasks if t.get("id") not in rank]


def _bundle_from(tasks: List[dict], mode: str, effort: str,
                 max_size: int) -> Bundle:
    """Bildet das kleinste sinnvolle Buendel: EIN Projekt, bis zu `max_size` Aufgaben.

    Nicht nach Anzahl buendeln und keine unabhaengigen Projekte mischen — ein
    Buendel soll ein ueberpruefbares Zwischenziel ergeben.
    """
    first = tasks[0]
    project = first.get("project_path") or ""
    root = first.get("root_id") or ""
    same = [t for t in tasks
            if (t.get("project_path") or "") == project
            and (t.get("root_id") or "") == root][:max_size]
    return Bundle(mode=mode, effort=effort, root_id=root,
                  project_path=project, tasks=same)


def taskwriter_unclassified_bundle(
    config: SelectorConfig, store: TaskStore, locks: LockView
) -> Optional[Bundle]:
    """Dringende Writer-Arbeit auf Taskebene, noch vor Projekt-Reviews."""
    open_tasks = store.list(status="open", limit=500)
    unclassified = [t for t in open_tasks
                    if not t.get("effort") and _reachable(t, locks)]
    if not unclassified:
        return None
    surface = [t for t in unclassified if not t.get("project_path")]
    pool = surface or unclassified
    return _bundle_from(pool, SURFACE if surface else DEEP,
                        "", config.max_bundle_size)


def _writer_bundle(config: SelectorConfig, store: TaskStore,
                   locks: LockView,
                   after_project: str = "") -> Optional[Bundle]:
    """Die Auswahl des TASKWRITER — eine ANDERE als die des Solvers.

    Aufgedeckt vom TASKWRITER-Loop (2026-07-14): Er bekam dieselbe Auswahl wie
    der TASKSOLVER und damit systematisch NICHTS. Der Solver waehlt nur, was
    klassifiziert ist — aber der Writer ist ja gerade derjenige, der einstuft.
    Er haette nie etwas zu tun bekommen, sobald der Solver-Vorrat leer ist.

    Seine Arbeit ist die INVERSE:

      1. UNKLASSIFIZIERTE Aufgaben nachstufen (effort leer). Sie sind fuer den
         Selektor unsichtbar und liegen sonst fuer immer still — das ist die
         dringlichste Writer-Arbeit ueberhaupt.
      2. Ist alles eingestuft: das naechste Projekt, das noch GAR KEINE
         Aufgaben hat. Dort ist der Rueckstand per Definition unerfasst.

    Der Aufwands-Gate gilt fuer ihn NICHT — er fuehrt nichts aus, er beschreibt
    nur. Aber der Lock-Scope gilt: In ein gesperrtes Projekt schreibt auch der
    Writer keine Steuerdateien.
    """
    # 1. Unklassifizierte zuerst.
    unclassified = taskwriter_unclassified_bundle(config, store, locks)
    if unclassified is not None:
        return unclassified

    # 2. Alles eingestuft -> ein Projekt suchen, das noch keine Aufgaben hat.
    if not config.deep_enabled or not config.projects:
        return None

    # Pfade NORMALISIEREN, nicht als Strings vergleichen: "/p/x" und "\p\x"
    # sind derselbe Ort, aber nicht derselbe String. Ohne das haelt der Writer
    # laengst erfasste Projekte fuer unberuehrt und schreibt Aufgaben doppelt.
    def _key(raw) -> str:
        try:
            return Path(raw).as_posix().rstrip("/").lower()
        except (TypeError, ValueError):
            return str(raw).lower()

    # Fuer die Projekthistorie darf es kein Fenster geben: Sobald die DB mehr
    # als 1000 Eintraege hatte, verschwanden aeltere Projekte aus `known` und
    # wurden dem Writer faelschlich erneut als unberuehrt angeboten.
    known = {_key(t.get("project_path") or "")
             for t in store.list(limit=None, include_done=True)
             if t.get("project_path")}

    candidates = []
    for project in config.projects:
        if _key(project.path) in known:
            continue
        if not locks.allows_selection(project.path):
            continue   # Gesperrt: der Writer schreibt dort keine Steuerdateien.
        candidates.append(project)

    if not candidates:
        return None

    after_key = _key(after_project) if after_project else ""
    if after_key:
        for index, project in enumerate(candidates):
            if _key(project.path) == after_key:
                candidates = candidates[index + 1:] + candidates[:index + 1]
                break

    project = candidates[0]
    return Bundle(mode=DEEP, effort="", root_id=project.root_id,
                  project_path=str(project.path), tasks=[])


def _maintainer_bundle(config: SelectorConfig, store: TaskStore,
                       locks: LockView,
                       after_project: str = "") -> Optional[Bundle]:
    """Die Auswahl des MAINTAINER — wieder eine ANDERE.

    Aufgedeckt vom MAINTAINER-Loop (2026-07-14): Er fiel in den TASKSOLVER-Zweig
    und bekam damit systematisch DASSELBE Projekt zugewiesen wie der Solver.
    Ergebnis: 2 von 2 Zuweisungen kollidierten — der Solver lockte das Projekt,
    der Maintainer stand Sekunden vor dem Schreiben vor einem fremden Lock.
    Das war keine Race Condition, sondern eine garantierte Kollision.

    Der Maintainer arbeitet an PROJEKTEN, nicht an Aufgaben. Seine Auswahl:

      Das naechste erreichbare Projekt, an dem gerade NIEMAND arbeitet.

    "Niemand" heisst zweierlei — beides muss geprueft werden, keines genuegt
    allein:
      * kein fremder Lock (der Solver setzt ihn, BEVOR er anfaengt), UND
      * keine aktive oder nichtterminal zugewiesene Aufgabe (der Solver hat
        sie geclaimt, aber seinen Lock vielleicht noch nicht gesetzt — genau
        dieses Zeitfenster liess die Kollisionen entstehen). Ein historisches
        ``assigned_to`` an ``done``/``cancelled`` ist keine laufende Arbeit.
    """
    if not config.projects:
        return None

    def _key(raw) -> str:
        try:
            return Path(raw).as_posix().rstrip("/").lower()
        except (TypeError, ValueError):
            return str(raw).lower()

    busy = set()        # jemand arbeitet dort: aktiv ODER nichtterminal geclaimt
    touched = set()     # hat ueberhaupt schon Aufgaben (egal welchen Status)

    # Auch aktive/zugewiesene Arbeit jenseits eines festen Verlaufsfensters
    # muss ein Projekt fuer den Maintainer weiterhin als beschaeftigt markieren.
    for task in store.list(limit=None, include_done=True):
        project = task.get("project_path")
        if not project:
            continue
        key = _key(project)
        touched.add(key)
        status = task.get("status")
        if status == "active" or (
            status not in {"done", "cancelled"} and task.get("assigned_to")
        ):
            busy.add(key)

    # Der Maintainer bevorzugt Projekte, die schon BERUEHRT sind — und das ist
    # kein Trick zur Kollisionsvermeidung, sondern inhaltlich richtig:
    #
    #   Wo schon gearbeitet wurde, ist Doku-Drift entstanden: veraltete
    #   STATE.md, unvollstaendige Architekturtabellen, Logs, die gewachsen
    #   sind. Ein unberuehrtes Projekt hat nichts, was aufzuraeumen waere —
    #   dort fehlt die ERFASSUNG, und die ist die Arbeit des TASKWRITER.
    #
    # Damit greifen die beiden Rollen naturgemaess auf disjunkte Mengen zu:
    # der Writer sucht unberuehrte Projekte, der Maintainer beruehrte. Eine
    # kuenstliche Trennung (etwa: "der Maintainer laeuft die Liste rueckwaerts")
    # waere ein Pflaster gewesen, das bei wenigen Projekten wieder kollidiert.
    # Touched projects bleiben die bevorzugte Gruppe, unberuehrte Projekte
    # der Fallback. Innerhalb der Gesamtmenge wird aber nach dem letzten
    # Cursor weitergelaufen. So wird ein freies Projekt nicht bei jedem neuen
    # CLI-Prozess erneut als erstes geliefert.
    candidates = []
    seen = set()
    for prefer_touched in (True, False):
        for project in config.projects:
            key = _key(project.path)
            if key in seen or key in busy:
                continue
            if (key in touched) != prefer_touched:
                continue
            if not locks.allows_selection(project.path):
                continue
            seen.add(key)
            candidates.append(project)

    if not candidates:
        return None

    after_key = _key(after_project) if after_project else ""
    if after_key:
        for index, project in enumerate(candidates):
            if _key(project.path) == after_key:
                candidates = candidates[index + 1:] + candidates[:index + 1]
                break

    project = candidates[0]
    return Bundle(mode=DEEP, effort="", root_id=project.root_id,
                  project_path=str(project.path), tasks=[])


def review_project_candidates(
    config: SelectorConfig,
    store: TaskStore,
    role: str,
) -> List:
    """Strukturell zulässige Projekte vor Pool-, Hash- und Lockbewertung.

    Der Pool braucht die vollständige Liste, um `lock` diagnostizieren und
    effort/last_presented korrekt sortieren zu können. Nur echte fachliche
    Parallelbelegung des MAINTAINER-Projekts wird hier vorab entfernt.
    """
    if not config.deep_enabled:
        return []
    if role == "taskwriter":
        return list(config.projects)
    if role != "maintainer":
        return []

    busy = set()
    for task in store.list(limit=None, include_done=True):
        project = task.get("project_path")
        if not project:
            continue
        status = task.get("status")
        if status == "active" or (
            status not in {"done", "cancelled"} and task.get("assigned_to")
        ):
            busy.add(_project_key(project))
    return [
        project for project in config.projects
        if _project_key(project.path) not in busy
    ]


def next_bundle(config: SelectorConfig, store: TaskStore,
                locks: LockView, role: str = "tasksolver",
                after_project: str = "",
                deferred_task_ids: Optional[List[int]] = None) -> Optional[Bundle]:
    """Was ist als Naechstes dran? None = nichts zu tun.

    Gibt der Selektor None zurueck, endet der Durchlauf EHRLICH als Leerlauf —
    statt dass das Modell sich Arbeit sucht, um den Loop zu fuellen.

    Die Rolle bestimmt die Auswahl: Der TASKWRITER sucht, was NICHT eingestuft
    ist; der TASKSOLVER genau das Gegenteil. Beide dieselbe Auswahl zu geben,
    hiesse dem Writer systematisch nichts zu geben.
    """
    if role == "taskwriter":
        return _writer_bundle(
            config, store, locks, after_project=after_project
        )
    if role == "maintainer":
        return _maintainer_bundle(config, store, locks, after_project=after_project)

    efforts = config.allowed_efforts()

    # effort ist die primaere Dimension: erst ALLE easy (Oberflaeche wie Tiefe),
    # dann erst medium. Deshalb die aeussere Schleife ueber den Aufwand.
    for effort in efforts:
        # 1. Oberflaeche zuerst — sie ist billig und entlastet sofort.
        surface = _candidates(store, effort, locks, surface=True)
        # Wurzelaufgaben haben kein Projekt — der Projekt-Cursor greift dort
        # nicht. Fuer sie ist der Revolver der einzige Weg, eine blockierte
        # Aufgabe zurueckzustellen, ohne ihre Etiketten zu verfaelschen.
        surface = _apply_task_revolver(surface, deferred_task_ids)
        if surface:
            return _bundle_from(surface, SURFACE, effort, config.max_bundle_size)

        # 2. Dann in die Projekte.
        if not config.deep_enabled:
            continue
        deep = _candidates(store, effort, locks, surface=False)
        deep = _apply_task_revolver(deep, deferred_task_ids)
        deep = _rotate_solver_candidates(deep, after_project)
        if deep:
            return _bundle_from(deep, DEEP, effort, config.max_bundle_size)

        # 3. Dieser Aufwandsgrad ist systemweit erschoepft -> naechsthoeherer.
        if not config.easy_first_globally:
            # Sonst waere die Rotation primaer und der Aufwand sekundaer —
            # genau die Reihenfolge, die der Nutzer verworfen hat.
            break

    return None
