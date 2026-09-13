# -*- coding: utf-8 -*-
"""`taskplan init` — der Erstlauf, den die Rollen voraussetzen duerfen.

WARUM ES DAS GIBT (T-20260831-555678565)
Nach der Wiederherstellung der Discovery fand TASKPLAN 389 Projekte. Der erste
`python -m taskplan next --role taskwriter --json` blieb daraufhin lange ohne
jede Ausgabe -- der Prozess lief, verbrauchte CPU und sagte nichts. Ursache war
kein Fehler, sondern Arbeit: Der Review-Pool musste fuer jedes Projekt einen
vollstaendigen Inhalts-Hash bilden, und auf einem OneDrive-Baum skaliert das mit
dem gesamten Dateivolumen.

Das Problem daran ist nicht die Dauer, sondern WO sie anfaellt: in einem Aufruf,
den ein Loop-Agent fuer eine schnelle Frage haelt. Deshalb bekommt dieser Lauf
hier einen eigenen, sichtbaren Ort mit Fortschrittsanzeige -- und die Rollen
bekommen ein Gate, das fail-closed sperrt, solange er nicht nachweislich
durchgelaufen ist.

ZWEI DINGE, DIE NICHT VERWECHSELT WERDEN DUERFEN
  * ``taskplan_project_index`` ist ein BESCHLEUNIGER: gemessene Fingerabdruecke
    und Siegelwerte, jederzeit wegwerfbar, jederzeit neu erzeugbar.
  * ``taskplan_project_reviews`` (im Review-Pool) ist FACHLICHER ZUSTAND:
    Praesentationen, Siegel, Leases. Die Initialisierung fasst diese Tabelle
    nicht an -- sie praesentiert kein Projekt, erzeugt keine Lease und
    aktiviert keinen Task.

DIE BEKANNTE GRENZE DES BESCHLEUNIGERS
Der Fingerabdruck einer Datei ist ``(relativer Pfad, Groesse, mtime_ns)``, nicht
ihr Inhalt. Eine Aenderung, die Groesse UND Zeitstempel exakt erhaelt, wird
deshalb nicht erkannt. Das ist bewusst in Kauf genommen: Die Alternative waere,
bei jedem `next` erneut jedes Byte zu lesen -- genau der Zustand, der dieses
Ticket ausgeloest hat. Wer den Verdacht hat, dass ein Siegel falsch ist, wirft
den Index mit ``taskplan init --rebuild`` weg.

NICHT VERWECHSELN MIT ``taskplan.api.init()``
Der gleichnamige API-Aufruf setzt nur die globale Client-Instanz (DB-Pfad,
Agent-Id) und hat mit der Readiness nichts zu tun. Der Befehlsname ``init`` ist
eine Nutzersetzung (Entscheid E05=A vom 2026-09-11) und bleibt deshalb so.

Aufrufe:
    python -m taskplan init
    python -m taskplan init --json
    python -m taskplan init --rebuild              # Index verwerfen, neu messen
    python -m taskplan init --skip-unreadable      # unlesbare Projekte zulassen
"""
from __future__ import annotations

import hashlib
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .review_pool import (
    ProjectDigest,
    ProjectHashError,
    digest_records,
    project_key,
    scan_project,
)

READINESS_SCHEMA = 1
REPAIR_COMMAND = "python -m taskplan init"

READINESS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS taskplan_project_index (
    project_key   TEXT PRIMARY KEY,
    project_path  TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    digest        TEXT NOT NULL,
    file_count    INTEGER NOT NULL,
    byte_count    INTEGER NOT NULL,
    computed_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS taskplan_readiness (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version    INTEGER NOT NULL,
    policy_signature  TEXT NOT NULL,
    projects_total    INTEGER NOT NULL,
    projects_indexed  INTEGER NOT NULL,
    projects_skipped  INTEGER NOT NULL,
    completed_at      TEXT NOT NULL,
    host              TEXT NOT NULL,
    duration_seconds  REAL NOT NULL
);
"""


def ensure_readiness_schema(conn) -> None:
    """Legt nur additive Tabellen an."""
    conn.executescript(READINESS_SCHEMA_SQL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def policy_signature(exclude: Iterable[str]) -> str:
    """Bindet die Readiness an Schema UND Ausschlussmuster.

    Aendert jemand die Excludes, sind alle gespeicherten Siegel fachlich etwas
    anderes. Die Readiness gilt dann als veraltet, statt still weiterzugelten.
    """
    payload = json.dumps(
        {"schema": READINESS_SCHEMA, "exclude": sorted(str(item) for item in exclude)},
        ensure_ascii=False, sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def fingerprint_records(records: Iterable[tuple[str, str, Path, int, int]]) -> str:
    """Billiger Aenderungsmarker aus Metadaten -- liest keine Dateiinhalte."""
    digest = hashlib.sha256()
    digest.update(b"taskplan-project-fingerprint-v1\0")
    for relative, kind, _target, size, mtime_ns in records:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(kind.encode("ascii"))
        digest.update(b"\0")
        digest.update(f"{size}:{mtime_ns}".encode("ascii"))
        digest.update(b"\0")
    return "fp1:" + digest.hexdigest()


def _index_row(conn, key: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT fingerprint, digest, file_count, byte_count "
        "FROM taskplan_project_index WHERE project_key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return {
        "fingerprint": row[0], "digest": row[1],
        "file_count": row[2], "byte_count": row[3],
    }


def cached_digest(
    store,
    path: str | Path,
    exclude: Iterable[str] = (),
) -> ProjectDigest:
    """Siegelwert eines Projekts -- gemessen, wenn noetig; sonst erinnert.

    Der Verzeichnislauf findet IMMER statt; gespart wird nur das Lesen der
    Dateiinhalte. Damit bleibt jede Aenderung an Pfad, Groesse oder Zeitstempel
    zuverlaessig sichtbar.
    """
    records = scan_project(path, exclude=exclude)
    fingerprint = fingerprint_records(records)
    key = project_key(path)
    conn = store._get_conn()
    try:
        ensure_readiness_schema(conn)
        row = _index_row(conn, key)
        if row and row["fingerprint"] == fingerprint:
            return ProjectDigest(
                value=row["digest"],
                file_count=row["file_count"],
                byte_count=row["byte_count"],
            )
    finally:
        store._close_conn(conn)

    digest = digest_records(records)

    conn = store._get_conn()
    try:
        ensure_readiness_schema(conn)
        conn.execute(
            "INSERT INTO taskplan_project_index "
            "(project_key, project_path, fingerprint, digest, file_count, "
            " byte_count, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_key) DO UPDATE SET "
            "project_path=excluded.project_path, "
            "fingerprint=excluded.fingerprint, digest=excluded.digest, "
            "file_count=excluded.file_count, byte_count=excluded.byte_count, "
            "computed_at=excluded.computed_at",
            (key, str(Path(path)), fingerprint, digest.value,
             digest.file_count, digest.byte_count, _now()),
        )
        conn.commit()
    finally:
        store._close_conn(conn)
    return digest


def readiness_row(store) -> Optional[dict[str, Any]]:
    conn = store._get_conn()
    try:
        ensure_readiness_schema(conn)
        row = conn.execute(
            "SELECT schema_version, policy_signature, projects_total, "
            "projects_indexed, projects_skipped, completed_at, host, "
            "duration_seconds FROM taskplan_readiness WHERE id = 1"
        ).fetchone()
    finally:
        store._close_conn(conn)
    if row is None:
        return None
    return {
        "schema_version": row[0], "policy_signature": row[1],
        "projects_total": row[2], "projects_indexed": row[3],
        "projects_skipped": row[4], "completed_at": row[5],
        "host": row[6], "duration_seconds": row[7],
    }


def readiness_status(store, exclude: Iterable[str] = ()) -> dict[str, Any]:
    """Darf eine Rolle starten? Immer mit Grund und Reparaturbefehl."""
    expected = policy_signature(exclude)
    row = readiness_row(store)
    if row is None:
        return {
            "ready": False, "state": "missing", "repair": REPAIR_COMMAND,
            "reason": (
                "TASKPLAN ist auf diesem System noch nicht initialisiert. "
                f"Einmalig ausfuehren: {REPAIR_COMMAND}"
            ),
        }
    if int(row["schema_version"]) != READINESS_SCHEMA:
        return {
            "ready": False, "state": "schema-outdated", "repair": REPAIR_COMMAND,
            "readiness": row,
            "reason": (
                f"Initialisierung stammt aus Schema {row['schema_version']}, "
                f"erwartet wird {READINESS_SCHEMA}. Erneut ausfuehren: "
                f"{REPAIR_COMMAND}"
            ),
        }
    if row["policy_signature"] != expected:
        return {
            "ready": False, "state": "policy-changed", "repair": REPAIR_COMMAND,
            "readiness": row,
            "reason": (
                "Die Ausschlussmuster des Review-Pools haben sich seit der "
                "Initialisierung geaendert; gespeicherte Siegel messen etwas "
                f"anderes. Erneut ausfuehren: {REPAIR_COMMAND}"
            ),
        }
    return {"ready": True, "state": "ready", "readiness": row, "reason": "", "repair": ""}


def clear_index(store) -> int:
    conn = store._get_conn()
    try:
        ensure_readiness_schema(conn)
        cursor = conn.execute("DELETE FROM taskplan_project_index")
        conn.execute("DELETE FROM taskplan_readiness WHERE id = 1")
        conn.commit()
        return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    finally:
        store._close_conn(conn)


def initialize(
    store,
    projects: Iterable[Any],
    *,
    exclude: Iterable[str] = (),
    rebuild: bool = False,
    skip_unreadable: bool = False,
    progress: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """Waermt den Index und markiert erst DANACH als bereit.

    Idempotent: Ein zweiter Lauf misst nur, was sich geaendert hat. Bricht der
    Lauf ab, wurde nichts markiert -- der bereits geschriebene Index bleibt als
    Teilfortschritt liegen und macht den Wiederanlauf billig.
    """
    exclude = tuple(exclude)
    if rebuild:
        clear_index(store)

    entries = [
        (Path(getattr(item, "path", item)), str(getattr(item, "root_id", "")))
        for item in projects
    ]
    total = len(entries)
    started = time.monotonic()
    indexed = 0
    failures: list[dict[str, str]] = []

    for position, (path, root_id) in enumerate(entries, start=1):
        if progress:
            progress({
                "phase": "project", "position": position, "total": total,
                "project_path": str(path), "root_id": root_id,
                "elapsed_seconds": round(time.monotonic() - started, 1),
            })
        try:
            cached_digest(store, path, exclude)
        except ProjectHashError as exc:
            failures.append({"project_path": str(path), "error": str(exc)})
            continue
        indexed += 1

    duration = round(time.monotonic() - started, 1)
    report = {
        "projects_total": total,
        "projects_indexed": indexed,
        "projects_skipped": len(failures),
        "failures": failures,
        "duration_seconds": duration,
        "policy_signature": policy_signature(exclude),
    }

    if failures and not skip_unreadable:
        report["ready"] = False
        report["reason"] = (
            f"{len(failures)} Projekt(e) konnten nicht gelesen werden. Ursache "
            "beheben und erneut ausfuehren, oder die Projekte mit "
            "--skip-unreadable bewusst auslassen."
        )
        return report

    conn = store._get_conn()
    try:
        ensure_readiness_schema(conn)
        conn.execute(
            "INSERT INTO taskplan_readiness "
            "(id, schema_version, policy_signature, projects_total, "
            " projects_indexed, projects_skipped, completed_at, host, "
            " duration_seconds) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "schema_version=excluded.schema_version, "
            "policy_signature=excluded.policy_signature, "
            "projects_total=excluded.projects_total, "
            "projects_indexed=excluded.projects_indexed, "
            "projects_skipped=excluded.projects_skipped, "
            "completed_at=excluded.completed_at, host=excluded.host, "
            "duration_seconds=excluded.duration_seconds",
            (READINESS_SCHEMA, report["policy_signature"], total, indexed,
             len(failures), _now(), platform.node(), duration),
        )
        conn.commit()
    finally:
        store._close_conn(conn)

    report["ready"] = True
    report["reason"] = ""
    return report
