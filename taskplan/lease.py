# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""
taskplan.lease -- Kanonischer Task-Lease-Kern (SALT) und Lifecycle-Werkzeug
===========================================================================

Setzt den gemeinsamen Taskkern-Vertrag (#2096, #2126, #2389, #2390, #2443, #2617)
um:
- Aufgabenarten (`task_kind`): ``commissioned`` ("beauftragt") und ``discovered`` ("erkannt")
- Ergebnisziel (`target_output`) und Bauort-Tag (`bauort:<modul-id>`)
- Fencing-basierte Leases (`take`, `take --new`, `renew`, `submit`, `return`, `block`, `reclaim`)
- Pflichtgrund bei `return` und `block` (verhindert begründungslose Leer-Rückgaben)
"""

from __future__ import annotations

import socket
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .client import TaskClient

TASK_KINDS = ("commissioned", "discovered")

TTL_PROFILES: dict[str, tuple[int, int]] = {
    "S": (900, 3600),        # 15 min TTL, 1h max
    "M": (1800, 14400),      # 30 min TTL, 4h max
    "L": (3600, 28800),      # 60 min TTL, 8h max
}

LEASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS taskplan_leases (
    task_id INTEGER PRIMARY KEY,
    lease_id TEXT NOT NULL,
    fence INTEGER NOT NULL DEFAULT 1,
    holder TEXT NOT NULL,
    origin_host TEXT NOT NULL,
    ttl_profile TEXT NOT NULL DEFAULT 'M',
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    max_expires_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    note TEXT DEFAULT '',
    result_summary TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_taskplan_leases_status ON taskplan_leases(status);
CREATE INDEX IF NOT EXISTS idx_taskplan_leases_holder ON taskplan_leases(holder);
"""

TASK_KERNEL_EXTRA_COLUMNS = {
    "task_kind": "TEXT DEFAULT 'commissioned'",
    "target_output": "TEXT DEFAULT ''",
    "bauort": "TEXT DEFAULT ''",
}


class TaskLeaseError(ValueError):
    """Basisfehler für ungültige oder abgelehnte Task-Lease-Operationen."""


class TaskLeaseConflictError(TaskLeaseError):
    """Konflikt bei Lease-Übernahme, Fence-Prüfung oder Ablauf."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_utc(raw: str | None) -> Optional[datetime]:
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class TaskLeaseRecord:
    task_id: int
    lease_id: str
    fence: int
    holder: str
    origin_host: str
    ttl_profile: str
    acquired_at: str
    expires_at: str
    max_expires_at: str
    status: str
    task_kind: str = "commissioned"
    target_output: str = ""
    bauort: str = ""
    note: str = ""
    result_summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskLeaseManager:
    """Verwaltet Task-Leases und Aufgabenarten auf einer `TaskClient`-Datenbank."""

    def __init__(self, client: TaskClient | str | Path | None = None) -> None:
        if isinstance(client, TaskClient):
            self.client = client
        else:
            self.client = TaskClient(db_path=str(client) if client else None)
        self._ensure_lease_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.client.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_lease_schema(self) -> None:
        with self._get_conn() as conn:
            conn.executescript(LEASE_SCHEMA_SQL)
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(rinnsal_tasks)").fetchall()
            }
            for col_name, col_def in TASK_KERNEL_EXTRA_COLUMNS.items():
                if col_name not in existing:
                    conn.execute(
                        f"ALTER TABLE rinnsal_tasks ADD COLUMN {col_name} {col_def}"
                    )
            conn.commit()

    def list_tasks(
        self,
        *,
        status: Optional[str] = None,
        task_kind: Optional[str] = None,
        origin_host: Optional[str] = None,
        include_done: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self.reclaim_expired()
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            conditions.append("t.status = ?")
            params.append(status)
        elif not include_done:
            conditions.append("t.status NOT IN ('done', 'cancelled')")
        if task_kind:
            if task_kind not in TASK_KINDS:
                raise TaskLeaseError(f"Ungültige Aufgabenart: {task_kind!r}")
            conditions.append("COALESCE(t.task_kind, 'commissioned') = ?")
            params.append(task_kind)
        if origin_host:
            conditions.append("t.origin_host = ?")
            params.append(origin_host)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(max(1, min(limit, 500)))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT t.id, t.title, t.description, t.status, t.priority,
                       t.assigned_to, t.delegation_status, t.origin_host,
                       COALESCE(t.task_kind, 'commissioned') AS task_kind,
                       COALESCE(t.target_output, '') AS target_output,
                       COALESCE(t.bauort, '') AS bauort,
                       l.lease_id, l.fence, l.holder, l.expires_at, l.status AS lease_status
                FROM rinnsal_tasks t
                LEFT JOIN taskplan_leases l ON l.task_id = t.id AND l.status = 'active'
                {where}
                ORDER BY t.id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def reclaim_expired(self, now: Optional[datetime] = None) -> list[int]:
        ref_now = now or _utc_now()
        now_iso = _fmt_utc(ref_now)
        reclaimed: list[int] = []
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT task_id, lease_id, expires_at FROM taskplan_leases WHERE status = 'active'"
            ).fetchall()
            for row in rows:
                exp = _parse_utc(row["expires_at"])
                if exp is not None and exp <= ref_now:
                    tid = int(row["task_id"])
                    conn.execute(
                        """
                        UPDATE taskplan_leases
                        SET status = 'expired', note = 'lease_expired', updated_at = ?
                        WHERE task_id = ? AND lease_id = ?
                        """,
                        (now_iso, tid, row["lease_id"]),
                    )
                    conn.execute(
                        """
                        UPDATE rinnsal_tasks
                        SET status = 'open', assigned_to = '', delegation_status = 'lease_expired', updated_at = ?
                        WHERE id = ? AND status = 'active'
                        """,
                        (now_iso, tid),
                    )
                    reclaimed.append(tid)
            if reclaimed:
                conn.commit()
        return reclaimed

    def take(
        self,
        task_id: Optional[int] = None,
        *,
        holder: str,
        origin_host: Optional[str] = None,
        ttl_profile: str = "M",
        create_new: bool = False,
        title: str = "",
        description: str = "",
        priority: str = "medium",
        task_kind: str = "commissioned",
        target_output: str = "",
        project_path: str = "",
        bauort: str = "",
        now: Optional[datetime] = None,
    ) -> TaskLeaseRecord:
        holder_clean = (holder or "").strip()
        if not holder_clean:
            raise TaskLeaseError("holder (--by) darf nicht leer sein")
        if ttl_profile not in TTL_PROFILES:
            raise TaskLeaseError(f"Ungültiges TTL-Profil {ttl_profile!r}; erlaubt: {tuple(TTL_PROFILES)}")
        if task_kind not in TASK_KINDS:
            raise TaskLeaseError(f"Ungültige Aufgabenart {task_kind!r}; erlaubt: {TASK_KINDS}")

        self.reclaim_expired(now=now)
        ref_now = now or _utc_now()
        now_iso = _fmt_utc(ref_now)
        host = (origin_host or socket.gethostname() or "localhost").strip()
        ttl_sec, max_sec = TTL_PROFILES[ttl_profile]
        expires_iso = _fmt_utc(ref_now + timedelta(seconds=ttl_sec))
        max_expires_iso = _fmt_utc(ref_now + timedelta(seconds=max_sec))

        if create_new:
            if not title.strip():
                raise TaskLeaseError("Für `take --new` ist `--title` erforderlich")
            tags = f"bauort:{bauort.strip()}" if bauort.strip() else ""
            created = self.client.add(
                title=title.strip(),
                description=description,
                priority=priority,
                tags=tags,
                project_path=project_path,
            )
            task_id = int(created["id"])
            with self._get_conn() as conn:
                conn.execute(
                    """
                    UPDATE rinnsal_tasks
                    SET created_by = ?, origin_host = ?, task_kind = ?, target_output = ?, bauort = ?
                    WHERE id = ?
                    """,
                    (holder_clean, host, task_kind, target_output.strip(), bauort.strip(), task_id),
                )
                conn.commit()

        if task_id is None:
            raise TaskLeaseError("task_id oder create_new=True ist erforderlich")

        with self._get_conn() as conn:
            task_row = conn.execute(
                """
                SELECT id, status, COALESCE(task_kind, 'commissioned') AS task_kind,
                       COALESCE(target_output, '') AS target_output,
                       COALESCE(bauort, '') AS bauort
                FROM rinnsal_tasks WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise TaskLeaseError(f"Task #{task_id} existiert nicht")
            if task_row["status"] in ("done", "cancelled"):
                raise TaskLeaseConflictError(
                    f"Task #{task_id} ist bereits abgeschlossen ({task_row['status']})"
                )

            existing = conn.execute(
                "SELECT * FROM taskplan_leases WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            next_fence = 1
            if existing is not None:
                if existing["status"] == "active":
                    exp = _parse_utc(existing["expires_at"])
                    if exp is not None and exp > ref_now and existing["holder"] != holder_clean:
                        raise TaskLeaseConflictError(
                            f"Task #{task_id} ist bereits durch {existing['holder']!r} bis {existing['expires_at']} geleast"
                        )
                next_fence = int(existing["fence"]) + 1

            lease_id = str(uuid.uuid4())
            eff_kind = task_row["task_kind"] or task_kind
            eff_target = target_output.strip() or task_row["target_output"] or ""
            eff_bauort = bauort.strip() or task_row["bauort"] or ""

            conn.execute(
                """
                INSERT INTO taskplan_leases
                    (task_id, lease_id, fence, holder, origin_host, ttl_profile,
                     acquired_at, expires_at, max_expires_at, status, note, result_summary, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', '', '', ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    lease_id = excluded.lease_id,
                    fence = excluded.fence,
                    holder = excluded.holder,
                    origin_host = excluded.origin_host,
                    ttl_profile = excluded.ttl_profile,
                    acquired_at = excluded.acquired_at,
                    expires_at = excluded.expires_at,
                    max_expires_at = excluded.max_expires_at,
                    status = 'active',
                    note = '',
                    Updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    lease_id,
                    next_fence,
                    holder_clean,
                    host,
                    ttl_profile,
                    now_iso,
                    expires_iso,
                    max_expires_iso,
                    now_iso,
                ),
            )
            conn.execute(
                """
                UPDATE rinnsal_tasks
                SET status = 'active', assigned_to = ?, delegation_status = 'leased',
                    task_kind = ?, target_output = ?, bauort = ?, updated_at = ?
                WHERE id = ?
                """,
                (holder_clean, eff_kind, eff_target, eff_bauort, now_iso, task_id),
            )
            conn.commit()

        return TaskLeaseRecord(
            task_id=task_id,
            lease_id=lease_id,
            fence=next_fence,
            holder=holder_clean,
            origin_host=host,
            ttl_profile=ttl_profile,
            acquired_at=now_iso,
            expires_at=expires_iso,
            max_expires_at=max_expires_iso,
            status="active",
            task_kind=eff_kind,
            target_output=eff_target,
            bauort=eff_bauort,
        )

    def _verify_active_lease(
        self,
        conn: sqlite3.Connection,
        task_id: int,
        lease_id: str,
        fence: int,
        now: datetime,
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM taskplan_leases WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None or row["status"] != "active":
            raise TaskLeaseConflictError(f"Kein aktives Lease für Task #{task_id}")
        if row["lease_id"] != lease_id:
            raise TaskLeaseConflictError(f"lease_id passt nicht zum aktiven Lease von Task #{task_id}")
        if int(row["fence"]) != int(fence):
            raise TaskLeaseConflictError(
                f"Stale Fence für Task #{task_id}: erwartet {row['fence']}, erhalten {fence}"
            )
        exp = _parse_utc(row["expires_at"])
        if exp is not None and exp <= now:
            raise TaskLeaseConflictError(f"Lease für Task #{task_id} ist abgelaufen ({row['expires_at']})")
        return row

    def renew(
        self,
        task_id: int,
        *,
        lease_id: str,
        fence: int,
        now: Optional[datetime] = None,
    ) -> TaskLeaseRecord:
        ref_now = now or _utc_now()
        now_iso = _fmt_utc(ref_now)
        with self._get_conn() as conn:
            row = self._verify_active_lease(conn, task_id, lease_id, fence, ref_now)
            ttl_sec, _ = TTL_PROFILES.get(row["ttl_profile"], TTL_PROFILES["M"])
            max_exp = _parse_utc(row["max_expires_at"]) or (ref_now + timedelta(seconds=ttl_sec))
            if ref_now >= max_exp:
                raise TaskLeaseConflictError(f"Maximale Gesamtlaufzeit für Task #{task_id} erreicht")
            new_exp = min(ref_now + timedelta(seconds=ttl_sec), max_exp)
            new_exp_iso = _fmt_utc(new_exp)
            new_fence = int(row["fence"]) + 1
            conn.execute(
                """
                UPDATE taskplan_leases
                SET fence = ?, expires_at = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (new_fence, new_exp_iso, now_iso, task_id),
            )
            task_row = conn.execute(
                """
                SELECT COALESCE(task_kind, 'commissioned') AS task_kind,
                       COALESCE(target_output, '') AS target_output,
                       COALESCE(bauort, '') AS bauort
                FROM rinnsal_tasks WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            conn.commit()

        return TaskLeaseRecord(
            task_id=task_id,
            lease_id=lease_id,
            fence=new_fence,
            holder=row["holder"],
            origin_host=row["origin_host"],
            ttl_profile=row["ttl_profile"],
            acquired_at=row["acquired_at"],
            expires_at=new_exp_iso,
            max_expires_at=row["max_expires_at"],
            status="active",
            task_kind=task_row["task_kind"] if task_row else "commissioned",
            target_output=task_row["target_output"] if task_row else "",
            bauort=task_row["bauort"] if task_row else "",
        )

    def submit(
        self,
        task_id: int,
        *,
        lease_id: str,
        fence: int,
        result: str,
        now: Optional[datetime] = None,
    ) -> TaskLeaseRecord:
        result_clean = (result or "").strip()
        if not result_clean:
            raise TaskLeaseError("Ergebnis (--result) darf bei submit nicht leer sein")
        ref_now = now or _utc_now()
        now_iso = _fmt_utc(ref_now)
        with self._get_conn() as conn:
            row = self._verify_active_lease(conn, task_id, lease_id, fence, ref_now)
            conn.execute(
                """
                UPDATE taskplan_leases
                SET status = 'submitted', result_summary = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (result_clean, now_iso, task_id),
            )
            conn.execute(
                """
                UPDATE rinnsal_tasks
                SET status = 'done', done_at = ?, delegation_status = 'submitted', updated_at = ?
                WHERE id = ?
                """,
                (now_iso, now_iso, task_id),
            )
            task_row = conn.execute(
                """
                SELECT COALESCE(task_kind, 'commissioned') AS task_kind,
                       COALESCE(target_output, '') AS target_output,
                       COALESCE(bauort, '') AS bauort
                FROM rinnsal_tasks WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            conn.commit()

        return TaskLeaseRecord(
            task_id=task_id,
            lease_id=lease_id,
            fence=int(row["fence"]),
            holder=row["holder"],
            origin_host=row["origin_host"],
            ttl_profile=row["ttl_profile"],
            acquired_at=row["acquired_at"],
            expires_at=row["expires_at"],
            max_expires_at=row["max_expires_at"],
            status="submitted",
            task_kind=task_row["task_kind"] if task_row else "commissioned",
            target_output=task_row["target_output"] if task_row else "",
            bauort=task_row["bauort"] if task_row else "",
            result_summary=result_clean,
        )

    def return_lease(
        self,
        task_id: int,
        *,
        lease_id: str,
        fence: int,
        reason: str,
        now: Optional[datetime] = None,
    ) -> TaskLeaseRecord:
        reason_clean = (reason or "").strip()
        if not reason_clean:
            raise TaskLeaseError("Rückgabegrund (--reason) ist Pflicht (keine leere Lease-Rückgabe)")
        ref_now = now or _utc_now()
        now_iso = _fmt_utc(ref_now)
        with self._get_conn() as conn:
            row = self._verify_active_lease(conn, task_id, lease_id, fence, ref_now)
            conn.execute(
                """
                UPDATE taskplan_leases
                SET status = 'returned', note = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (reason_clean, now_iso, task_id),
            )
            conn.execute(
                """
                UPDATE rinnsal_tasks
                SET status = 'open', assigned_to = '', delegation_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (f"returned:{reason_clean[:80]}", now_iso, task_id),
            )
            task_row = conn.execute(
                """
                SELECT COALESCE(task_kind, 'commissioned') AS task_kind,
                       COALESCE(target_output, '') AS target_output,
                       COALESCE(bauort, '') AS bauort
                FROM rinnsal_tasks WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            conn.commit()

        return TaskLeaseRecord(
            task_id=task_id,
            lease_id=lease_id,
            fence=int(row["fence"]),
            holder=row["holder"],
            origin_host=row["origin_host"],
            ttl_profile=row["ttl_profile"],
            acquired_at=row["acquired_at"],
            expires_at=row["expires_at"],
            max_expires_at=row["max_expires_at"],
            status="returned",
            task_kind=task_row["task_kind"] if task_row else "commissioned",
            target_output=task_row["target_output"] if task_row else "",
            bauort=task_row["bauort"] if task_row else "",
            note=reason_clean,
        )

    def block(
        self,
        task_id: int,
        *,
        reason: str,
        lease_id: Optional[str] = None,
        fence: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        reason_clean = (reason or "").strip()
        if not reason_clean:
            raise TaskLeaseError("Blockiergrund (--reason) ist Pflicht")
        ref_now = now or _utc_now()
        now_iso = _fmt_utc(ref_now)
        with self._get_conn() as conn:
            if lease_id is not None and fence is not None:
                self._verify_active_lease(conn, task_id, lease_id, fence, ref_now)
            conn.execute(
                """
                UPDATE taskplan_leases
                SET status = 'blocked', note = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (reason_clean, now_iso, task_id),
            )
            cur = conn.execute(
                """
                UPDATE rinnsal_tasks
                SET status = 'open', assigned_to = '', delegation_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (f"blocked:{reason_clean[:80]}", now_iso, task_id),
            )
            if cur.rowcount == 0:
                raise TaskLeaseError(f"Task #{task_id} existiert nicht")
            conn.commit()
        return {"task_id": task_id, "status": "blocked", "reason": reason_clean}
