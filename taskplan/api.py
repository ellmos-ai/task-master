# -*- coding: utf-8 -*-
"""
taskplan High-Level API
=========================

Convenience-Funktionen fuer schnellen Zugriff ohne explizite Client-Instanz.
Singleton-Pattern mit globaler Default-DB.

Verwendung:
    from taskplan import api as tasks

    tasks.init(agent_id="opus")
    tasks.add("Feature X implementieren", priority="high")
    tasks.add("Bug fixen", description="Encoding-Problem")

    for t in tasks.list():
        print(f"[{t['id']}] {t['title']} ({t['status']})")

    tasks.done(1)
    tasks.activate(2)

Author: Lukas Geiger
License: MIT
"""
from typing import Optional, List, Dict

from .client import TaskClient

_client: Optional[TaskClient] = None


def init(
    db_path: Optional[str] = None,
    agent_id: str = "default"
) -> TaskClient:
    """Initialisiert die globale Task-Instanz.

    Ohne db_path gilt der Default des Clients
    (ENV TASKPLAN_DB > ENV RINNSAL_DB > ~/.taskplan/taskplan.db).
    """
    global _client
    _client = TaskClient(
        db_path=db_path,
        agent_id=agent_id
    )
    return _client


def get_client() -> TaskClient:
    """Gibt die globale Client-Instanz zurueck (lazy init)."""
    global _client
    if _client is None:
        _client = TaskClient(agent_id="default")
    return _client


def set_agent(agent_id: str) -> None:
    """Setzt die Agent-ID fuer neue Tasks."""
    client = get_client()
    client.agent_id = agent_id


# === Task Operations ===

def add(title: str, description: str = "", priority: str = "medium",
        tags: str = "") -> Dict:
    """Erstellt einen neuen Task."""
    return get_client().add(title, description=description,
                            priority=priority, tags=tags)


def add_from_ticket(ticket_id: str, title: str, description: str = "",
                    priority: str = "medium", tags: str = "") -> Dict:
    """Leitet aus einem Ticket einen Task ab (Tickets != Tasks).

    Tickets (z. B. dateibasierte Ticket-Systeme, IDs wie T-YYYYMMDD-NN)
    KOENNEN zu Tasks fuehren, muessen es aber nicht. Diese Funktion ist die
    einzige Bruecke: Sie erzeugt einen ganz normalen Task und verweist ueber
    das Tag `ticket:<id>` auf das Quell-Ticket. Das Ticket selbst lebt
    unveraendert in seinem eigenen System weiter -- taskplan importiert,
    spiegelt oder verwaltet keine Tickets.
    """
    ref = f"ticket:{ticket_id}"
    combined = f"{tags},{ref}" if tags else ref
    return get_client().add(title, description=description,
                            priority=priority, tags=combined)


def list(status: Optional[str] = None, priority: Optional[str] = None,
         include_done: bool = False, limit: Optional[int] = 50) -> List[Dict]:
    """Listet Tasks auf (default: nur offene/aktive)."""
    return get_client().list(status=status, priority=priority,
                             include_done=include_done, limit=limit)


def get(task_id: int) -> Optional[Dict]:
    """Holt einen einzelnen Task."""
    return get_client().get(task_id)


def done(task_id: int) -> bool:
    """Markiert einen Task als erledigt."""
    return get_client().done(task_id)


def activate(task_id: int) -> bool:
    """Setzt einen Task auf 'active'."""
    return get_client().activate(task_id)


def cancel(task_id: int) -> bool:
    """Storniert einen Task."""
    return get_client().cancel(task_id)


def reopen(task_id: int) -> bool:
    """Oeffnet einen erledigten/stornierten Task erneut."""
    return get_client().reopen(task_id)


def update(task_id: int, title: Optional[str] = None,
           description: Optional[str] = None, priority: Optional[str] = None,
           tags: Optional[str] = None, effort: Optional[str] = None,
           scope: Optional[str] = None, project_path: Optional[str] = None,
           root_id: Optional[str] = None, source: Optional[str] = None) -> bool:
    """Aktualisiert Task-Felder — einschliesslich der nachtraeglichen Einstufung.

    Ohne `effort`/`scope` hier waere die Fassade eine Sackgasse: Der TASKWRITER
    benutzt sie, um Altlasten nachzustufen. Fehlt der Parameter, bleiben
    unklassifizierte Aufgaben fuer immer unsichtbar — der Solver fasst sie
    nicht an, und niemand kann sie einstufen.
    """
    return get_client().update(task_id, title=title, description=description,
                               priority=priority, tags=tags, effort=effort,
                               scope=scope, project_path=project_path,
                               root_id=root_id, source=source)


def classify(task_id: int, effort: str, scope: str = "local",
             project_path: str = "", root_id: str = "") -> bool:
    """Stuft eine Aufgabe nachtraeglich ein — die Kernaufgabe des TASKWRITER.

    Bequemer Weg fuer den haeufigsten Fall: Eine Altlast ohne `effort` sichtbar
    machen. Beruehrt weder Herkunft (`created_by`) noch Zuweisung.

        tasks.classify(42, effort="easy", project_path="/repos/foo", root_id="OSS")
    """
    return update(task_id, effort=effort, scope=scope,
                  project_path=project_path or None,
                  root_id=root_id or None)


def delete(task_id: int) -> bool:
    """Loescht einen Task permanent."""
    return get_client().delete(task_id)


def count() -> Dict:
    """Zaehlt Tasks nach Status."""
    return get_client().count()


# === Shortcuts ===

def next_task() -> Optional[Dict]:
    """Gibt den naechsten offenen Task mit hoechster Prioritaet zurueck."""
    tasks = get_client().list(status='open', limit=1)
    return tasks[0] if tasks else None


def active_tasks() -> List[Dict]:
    """Gibt alle aktiven Tasks zurueck."""
    return get_client().list(status='active')
