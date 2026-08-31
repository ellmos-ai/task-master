# -*- coding: utf-8 -*-
"""Hostlokaler Siegel- und Wiedervorlagepool für Projektrollen.

Der Pool liegt absichtlich neben ``rinnsal_tasks``. Eine Projektvorlage ist
weder ein Task noch ein Claim und darf deshalb keine Task-Herkunft oder
Zuweisung verändern. Der vollständige Vertrag steht in
``docs/LOCAL_REVIEW_POOL_SPEC.md``.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


REVIEW_ROLES = ("taskwriter", "maintainer")
REVIEW_EFFORTS = ("easy", "medium")

DEFAULT_EXCLUDED_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".venv", "venv",
    "node_modules", "dist", "build", "htmlcov", ".taskplan",
})
DEFAULT_EXCLUDED_FILES = (
    "LOCK.execution-contract.txt", ".taskplan-lock*", "*.pyc", "*.pyo",
    ".DS_Store", "Thumbs.db", ".coverage",
)

REVIEW_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS taskplan_project_reviews (
    role TEXT NOT NULL,
    project_key TEXT NOT NULL,
    project_path TEXT NOT NULL,
    root_id TEXT NOT NULL DEFAULT '',
    effort TEXT NOT NULL DEFAULT 'easy',
    sealed_hash TEXT,
    last_presented_at TEXT,
    presentation_id TEXT,
    presented_hash TEXT,
    presentation_lease_until TEXT,
    last_reviewed_at TEXT,
    result TEXT,
    next_due_at TEXT,
    deferred_until TEXT,
    deferred_hash TEXT,
    defer_reason TEXT,
    manual_unseal_at TEXT,
    manual_unseal_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (role, project_key),
    CHECK (role IN ('taskwriter', 'maintainer')),
    CHECK (effort IN ('easy', 'medium'))
);

CREATE INDEX IF NOT EXISTS idx_project_reviews_selection
ON taskplan_project_reviews(role, effort, last_presented_at);

CREATE TABLE IF NOT EXISTS taskplan_project_review_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    project_key TEXT NOT NULL,
    project_path TEXT NOT NULL,
    event TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    presentation_id TEXT,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_project_review_events_project
ON taskplan_project_review_events(role, project_key, id);
"""

_STATE_COLUMNS = (
    "role, project_key, project_path, root_id, effort, sealed_hash, "
    "last_presented_at, presentation_id, presented_hash, "
    "presentation_lease_until, last_reviewed_at, result, next_due_at, "
    "deferred_until, deferred_hash, defer_reason, manual_unseal_at, "
    "manual_unseal_reason, created_at, updated_at"
)
_STATE_KEYS = tuple(part.strip() for part in _STATE_COLUMNS.split(","))


class ProjectHashError(RuntimeError):
    """Der relevante Projektbestand konnte nicht stabil gelesen werden."""


class ReviewError(RuntimeError):
    """Basisklasse für Review-Vertragsfehler."""


class ReviewInputError(ReviewError, ValueError):
    """Ungültige Rolle, Aufwand oder fehlender Begründungstext."""


class ReviewConflict(ReviewError):
    """Präsentationstoken fehlt, ist abgelaufen oder wurde bereits verbraucht."""


@dataclass(frozen=True)
class ProjectDigest:
    value: str
    file_count: int
    byte_count: int


@dataclass(frozen=True)
class ReviewPolicy:
    enabled: bool = True
    review_interval_seconds: int = 7 * 24 * 60 * 60
    retry_interval_seconds: int = 60 * 60
    presentation_lease_seconds: int = 15 * 60
    default_effort: str = "easy"
    exclude: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.default_effort not in REVIEW_EFFORTS:
            raise ReviewInputError(
                f"default_effort muss einer von {REVIEW_EFFORTS} sein"
            )
        for name in (
            "review_interval_seconds",
            "retry_interval_seconds",
        ):
            if getattr(self, name) < 0:
                raise ReviewInputError(f"{name} darf nicht negativ sein")
        if self.presentation_lease_seconds <= 0:
            raise ReviewInputError("presentation_lease_seconds muss positiv sein")
        object.__setattr__(self, "exclude", tuple(self.exclude))


def ensure_review_schema(conn) -> None:
    """Legt nur additive Pooltabellen und -indizes an."""
    conn.executescript(REVIEW_SCHEMA_SQL)


def project_key(path: str | Path) -> str:
    """Hostlokaler, symlink-neutraler Schlüssel eines Projektpfads."""
    raw = os.path.expandvars(os.fspath(path))
    expanded = os.path.expanduser(raw)
    absolute = os.path.abspath(expanded)
    normalized = os.path.normcase(os.path.normpath(absolute))
    return unicodedata.normalize("NFC", normalized.replace("\\", "/"))


def _normalized_relative(parts: tuple[str, ...]) -> str:
    return "/".join(unicodedata.normalize("NFC", part) for part in parts)


def _matches_exclude(relative: str, patterns: Iterable[str]) -> bool:
    candidate = relative.replace("\\", "/")
    folded = candidate.casefold()
    parts = folded.split("/") if folded else []
    for raw in patterns:
        pattern = str(raw).replace("\\", "/").strip("/").casefold()
        if not pattern:
            continue
        if "/" in pattern:
            if fnmatch.fnmatchcase(folded, pattern):
                return True
            if pattern.endswith("/**") and folded == pattern[:-3].rstrip("/"):
                return True
        elif any(fnmatch.fnmatchcase(part, pattern) for part in parts):
            return True
    return False


def _feed_record(target, *fields: bytes) -> None:
    for field in fields:
        target.update(len(field).to_bytes(8, "big"))
        target.update(field)


def hash_project(
    path: str | Path,
    *,
    exclude: Iterable[str] = (),
) -> ProjectDigest:
    """Hasht den fachlich relevanten Projektbestand deterministisch.

    Reguläre Dateien werden nach relativem POSIX-Pfad sortiert. Symlinks gehen
    als Linkziel ein und werden nicht verfolgt. Jeder unklare Lesestand bricht
    fail-closed ab, statt ein scheinbar gültiges Siegel zu erzeugen.
    """
    root = Path(os.path.expandvars(os.fspath(path))).expanduser()
    try:
        if root.is_symlink() or not root.is_dir():
            raise ProjectHashError(f"Kein stabil lesbarer Projektordner: {root}")
    except OSError as exc:
        raise ProjectHashError(f"Projektordner nicht lesbar: {root}: {exc}") from exc

    patterns = tuple(DEFAULT_EXCLUDED_FILES) + tuple(exclude)
    records: list[tuple[str, str, Optional[Path]]] = []

    def walk(directory: Path, parts: tuple[str, ...]) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = list(iterator)
        except OSError as exc:
            raise ProjectHashError(f"Verzeichnis nicht lesbar: {directory}: {exc}") from exc
        entries.sort(
            key=lambda item: unicodedata.normalize("NFC", item.name).encode("utf-8")
        )
        for entry in entries:
            child_parts = parts + (entry.name,)
            relative = _normalized_relative(child_parts)
            try:
                is_link = entry.is_symlink()
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError as exc:
                raise ProjectHashError(f"Pfadtyp nicht lesbar: {relative}: {exc}") from exc

            if is_dir:
                if entry.name.casefold() in DEFAULT_EXCLUDED_DIRS:
                    continue
                if _matches_exclude(relative, exclude):
                    continue
                walk(Path(entry.path), child_parts)
                continue
            if _matches_exclude(relative, patterns):
                continue
            if is_link:
                records.append((relative, "link", Path(entry.path)))
            elif is_file:
                records.append((relative, "file", Path(entry.path)))
            else:
                raise ProjectHashError(f"Nicht unterstützter Sonderdateityp: {relative}")

    walk(root, ())
    records.sort(key=lambda row: row[0].encode("utf-8"))
    normalized_paths: set[str] = set()
    for relative, _kind, _target in records:
        if relative in normalized_paths:
            raise ProjectHashError(
                "Mehrdeutiger Projektpfad nach NFC-Normalisierung: "
                f"{relative}"
            )
        normalized_paths.add(relative)

    digest = hashlib.sha256()
    digest.update(b"taskplan-project-sha256-v1\0")
    byte_count = 0
    for relative, kind, target in records:
        relative_bytes = relative.encode("utf-8")
        assert target is not None
        if kind == "link":
            try:
                link_target = os.readlink(target)
            except OSError as exc:
                raise ProjectHashError(f"Symlink nicht lesbar: {relative}: {exc}") from exc
            _feed_record(
                digest, b"L", relative_bytes,
                unicodedata.normalize("NFC", link_target).encode("utf-8"),
            )
            continue

        try:
            before = target.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise ProjectHashError(f"Dateityp änderte sich beim Lesen: {relative}")
            content = hashlib.sha256()
            read_bytes = 0
            with target.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    content.update(chunk)
                    read_bytes += len(chunk)
            after = target.stat(follow_symlinks=False)
        except ProjectHashError:
            raise
        except OSError as exc:
            raise ProjectHashError(f"Datei nicht stabil lesbar: {relative}: {exc}") from exc
        before_signature = (
            before.st_dev, before.st_ino, before.st_size,
            getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000)),
        )
        after_signature = (
            after.st_dev, after.st_ino, after.st_size,
            getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000)),
        )
        if before_signature != after_signature or read_bytes != before.st_size:
            raise ProjectHashError(f"Datei änderte sich während des Lesens: {relative}")
        _feed_record(
            digest, b"F", relative_bytes, str(read_bytes).encode("ascii"),
            content.digest(),
        )
        byte_count += read_bytes

    return ProjectDigest(
        value=f"sha256-v1:{digest.hexdigest()}",
        file_count=len(records),
        byte_count=byte_count,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return _utc(parsed)


def _row_to_state(row) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return dict(zip(_STATE_KEYS, row))


class ReviewPool:
    """Persistente Eligibility-, Präsentations- und Siegelzustandsmaschine."""

    def __init__(
        self,
        store,
        *,
        policy: Optional[ReviewPolicy] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.store = store
        self.policy = policy or ReviewPolicy()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        conn = self.store._get_conn()
        try:
            ensure_review_schema(conn)
            conn.commit()
        finally:
            self.store._close_conn(conn)

    @staticmethod
    def _validate_role(role: str) -> str:
        normalized = str(role).strip().lower()
        if normalized not in REVIEW_ROLES:
            raise ReviewInputError(f"Review-Rolle muss einer von {REVIEW_ROLES} sein")
        return normalized

    def _now(self) -> datetime:
        return _utc(self.clock())

    def _state_in(self, conn, role: str, key: str) -> Optional[dict[str, Any]]:
        row = conn.execute(
            f"SELECT {_STATE_COLUMNS} FROM taskplan_project_reviews "
            "WHERE role = ? AND project_key = ?",
            (role, key),
        ).fetchone()
        return _row_to_state(row)

    def get_state(self, role: str, project: str | Path) -> Optional[dict[str, Any]]:
        role = self._validate_role(role)
        conn = self.store._get_conn()
        try:
            return self._state_in(conn, role, project_key(project))
        finally:
            self.store._close_conn(conn)

    def _decision(
        self,
        role: str,
        project: str | Path,
        *,
        root_id: str = "",
        effort: str = "",
        locked: bool = False,
        state: Optional[dict[str, Any]] = None,
        digest: Optional[ProjectDigest] = None,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        role = self._validate_role(role)
        path = str(Path(project))
        key = project_key(project)
        now = now or self._now()
        resolved_effort = (
            (state or {}).get("effort") or effort or self.policy.default_effort
        )
        if resolved_effort not in REVIEW_EFFORTS:
            resolved_effort = self.policy.default_effort
        base = {
            "role": role,
            "project_key": key,
            "project_path": path,
            "root_id": root_id or (state or {}).get("root_id") or "",
            "effort": resolved_effort,
            "eligible": False,
            "reason": "",
            "current_hash": digest.value if digest else None,
            "last_presented_at": (state or {}).get("last_presented_at"),
            "state": state,
        }
        if locked:
            base["reason"] = "lock"
            return base
        if digest is None:
            try:
                digest = hash_project(project, exclude=self.policy.exclude)
            except ProjectHashError as exc:
                base["reason"] = "hash_error"
                base["error"] = str(exc)
                return base
            base["current_hash"] = digest.value
        if not state:
            base.update(eligible=True, reason="never_presented")
            return base

        lease_until = _parse(state.get("presentation_lease_until"))
        if state.get("presentation_id") and lease_until and lease_until > now:
            base["reason"] = "presentation_lease"
            return base
        if not state.get("last_presented_at"):
            base.update(eligible=True, reason="never_presented")
            return base
        if state.get("deferred_hash") and digest.value != state.get("deferred_hash"):
            base.update(eligible=True, reason="hash_break")
            return base
        manual = _parse(state.get("manual_unseal_at"))
        reviewed = _parse(state.get("last_reviewed_at"))
        # ``complete`` leert den manuellen Break atomar. Ist danach wieder ein
        # Wert vorhanden, ist er fachlich neuer — auch wenn eine kontrollierte
        # Uhr beide Ereignisse auf denselben Tick legt.
        if manual and (reviewed is None or manual >= reviewed):
            base.update(eligible=True, reason="manual_unseal")
            return base
        deferred_until = _parse(state.get("deferred_until"))
        if deferred_until and deferred_until > now:
            base["reason"] = "deferred"
            return base
        if not state.get("sealed_hash"):
            base.update(eligible=True, reason="never_sealed")
            return base
        if digest.value != state.get("sealed_hash"):
            base.update(eligible=True, reason="hash_break")
            return base
        due = _parse(state.get("next_due_at"))
        if due is not None and due <= now:
            base.update(eligible=True, reason="due")
            return base
        base["reason"] = "unchanged_sealed"
        return base

    def status(
        self,
        role: str,
        project: str | Path,
        *,
        root_id: str = "",
        effort: str = "",
        locked: bool = False,
    ) -> dict[str, Any]:
        role = self._validate_role(role)
        state = self.get_state(role, project)
        return self._decision(
            role, project, root_id=root_id, effort=effort,
            locked=locked, state=state,
        )

    def _event(
        self,
        conn,
        *,
        role: str,
        key: str,
        path: str,
        event: str,
        at: str,
        presentation_id: Optional[str] = None,
        detail: str = "",
    ) -> None:
        conn.execute(
            "INSERT INTO taskplan_project_review_events "
            "(role, project_key, project_path, event, occurred_at, "
            " presentation_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (role, key, path, event, at, presentation_id, detail),
        )

    def _begin(self, conn) -> None:
        conn.execute("BEGIN IMMEDIATE")

    def _active_token(self, state: Optional[dict[str, Any]], token: str, now: datetime) -> bool:
        if not state or not token or state.get("presentation_id") != token:
            return False
        lease = _parse(state.get("presentation_lease_until"))
        return lease is not None and lease > now

    def present_next(
        self,
        role: str,
        projects: Iterable[Any],
        *,
        locked: Optional[Callable[[Path], bool]] = None,
    ) -> dict[str, Any]:
        role = self._validate_role(role)
        decisions = []
        for project in projects:
            path = Path(getattr(project, "path", project))
            root_id = str(getattr(project, "root_id", ""))
            effort = str(getattr(project, "effort", "") or "")
            is_locked = bool(locked(path)) if locked else False
            state = self.get_state(role, path)
            decisions.append(self._decision(
                role, path, root_id=root_id, effort=effort,
                locked=is_locked, state=state,
            ))

        eligible = [item for item in decisions if item["eligible"]]
        effort_rank = {"easy": 0, "medium": 1}
        eligible.sort(key=lambda item: (
            effort_rank[item["effort"]],
            0 if not item.get("last_presented_at") else 1,
            item.get("last_presented_at") or "",
            item["project_key"],
        ))

        for candidate in eligible:
            now = self._now()
            at = _iso(now)
            token = str(uuid.uuid4())
            lease_until = _iso(
                now + timedelta(seconds=self.policy.presentation_lease_seconds)
            )
            conn = self.store._get_conn()
            try:
                self._begin(conn)
                state = self._state_in(conn, role, candidate["project_key"])
                current = self._decision(
                    role,
                    candidate["project_path"],
                    root_id=candidate["root_id"],
                    effort=candidate["effort"],
                    state=state,
                    digest=ProjectDigest(candidate["current_hash"], 0, 0),
                    now=now,
                )
                if not current["eligible"]:
                    conn.rollback()
                    continue
                conn.execute(
                    "INSERT INTO taskplan_project_reviews "
                    "(role, project_key, project_path, root_id, effort, "
                    " last_presented_at, presentation_id, presented_hash, "
                    " presentation_lease_until, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(role, project_key) DO UPDATE SET "
                    "project_path=excluded.project_path, root_id=excluded.root_id, "
                    "effort=excluded.effort, "
                    "last_presented_at=excluded.last_presented_at, "
                    "presentation_id=excluded.presentation_id, "
                    "presented_hash=excluded.presented_hash, "
                    "presentation_lease_until=excluded.presentation_lease_until, "
                    "updated_at=excluded.updated_at",
                    (
                        role, candidate["project_key"], candidate["project_path"],
                        candidate["root_id"], current["effort"], at, token,
                        candidate["current_hash"], lease_until, at, at,
                    ),
                )
                self._event(
                    conn, role=role, key=candidate["project_key"],
                    path=candidate["project_path"], event="presented", at=at,
                    presentation_id=token,
                    detail=json.dumps({
                        "reason": current["reason"],
                        "hash": candidate["current_hash"],
                    }, ensure_ascii=False, sort_keys=True),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                self.store._close_conn(conn)
            presentation = {
                key: candidate[key] for key in (
                    "role", "project_key", "project_path", "root_id", "effort",
                    "current_hash", "reason",
                )
            }
            presentation.update({
                "presentation_id": token,
                "last_presented_at": at,
                "presentation_lease_until": lease_until,
            })
            return {"presentation": presentation, "decisions": decisions}
        return {"presentation": None, "decisions": decisions}

    def complete(
        self,
        role: str,
        project: str | Path,
        presentation_id: str,
        result: str,
    ) -> dict[str, Any]:
        role = self._validate_role(role)
        if not str(result).strip():
            raise ReviewInputError("Ein bestätigter Abschluss braucht ein Ergebnis")
        digest = hash_project(project, exclude=self.policy.exclude)
        key = project_key(project)
        now = self._now()
        at = _iso(now)
        next_due = _iso(now + timedelta(seconds=self.policy.review_interval_seconds))
        conn = self.store._get_conn()
        try:
            self._begin(conn)
            state = self._state_in(conn, role, key)
            if not self._active_token(state, presentation_id, now):
                raise ReviewConflict("Präsentationstoken fehlt, ist abgelaufen oder verbraucht")
            conn.execute(
                "UPDATE taskplan_project_reviews SET sealed_hash=?, "
                "last_reviewed_at=?, result=?, next_due_at=?, "
                "presentation_id=NULL, presented_hash=NULL, "
                "presentation_lease_until=NULL, deferred_until=NULL, "
                "deferred_hash=NULL, defer_reason=NULL, manual_unseal_at=NULL, "
                "manual_unseal_reason=NULL, updated_at=? "
                "WHERE role=? AND project_key=? AND presentation_id=?",
                (
                    digest.value, at, str(result), next_due, at,
                    role, key, presentation_id,
                ),
            )
            self._event(
                conn, role=role, key=key, path=str(Path(project)),
                event="sealed", at=at, presentation_id=presentation_id,
                detail=json.dumps({
                    "hash": digest.value, "result": str(result),
                    "next_due_at": next_due,
                }, ensure_ascii=False, sort_keys=True),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.store._close_conn(conn)
        state = self.get_state(role, project)
        assert state is not None
        return state

    def defer(
        self,
        role: str,
        project: str | Path,
        presentation_id: str,
        reason: str,
    ) -> dict[str, Any]:
        role = self._validate_role(role)
        if not str(reason).strip():
            raise ReviewInputError("Eine Deferierung braucht einen Grund")
        digest = hash_project(project, exclude=self.policy.exclude)
        key = project_key(project)
        now = self._now()
        at = _iso(now)
        deferred_until = _iso(now + timedelta(seconds=self.policy.retry_interval_seconds))
        conn = self.store._get_conn()
        try:
            self._begin(conn)
            state = self._state_in(conn, role, key)
            if not self._active_token(state, presentation_id, now):
                raise ReviewConflict("Präsentationstoken fehlt, ist abgelaufen oder verbraucht")
            conn.execute(
                "UPDATE taskplan_project_reviews SET deferred_until=?, "
                "deferred_hash=?, defer_reason=?, presentation_id=NULL, "
                "presented_hash=NULL, presentation_lease_until=NULL, updated_at=? "
                "WHERE role=? AND project_key=? AND presentation_id=?",
                (
                    deferred_until, digest.value, str(reason), at,
                    role, key, presentation_id,
                ),
            )
            self._event(
                conn, role=role, key=key, path=str(Path(project)),
                event="deferred", at=at, presentation_id=presentation_id,
                detail=json.dumps({
                    "hash": digest.value, "reason": str(reason),
                    "deferred_until": deferred_until,
                }, ensure_ascii=False, sort_keys=True),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.store._close_conn(conn)
        state = self.get_state(role, project)
        assert state is not None
        return state

    def unseal(self, role: str, project: str | Path, reason: str) -> dict[str, Any]:
        role = self._validate_role(role)
        if not str(reason).strip():
            raise ReviewInputError("Ein manueller Siegelbruch braucht einen Grund")
        key = project_key(project)
        path = str(Path(project))
        at = _iso(self._now())
        conn = self.store._get_conn()
        try:
            self._begin(conn)
            conn.execute(
                "INSERT INTO taskplan_project_reviews "
                "(role, project_key, project_path, effort, manual_unseal_at, "
                " manual_unseal_reason, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(role, project_key) DO UPDATE SET "
                "project_path=excluded.project_path, "
                "manual_unseal_at=excluded.manual_unseal_at, "
                "manual_unseal_reason=excluded.manual_unseal_reason, "
                "updated_at=excluded.updated_at",
                (
                    role, key, path, self.policy.default_effort,
                    at, str(reason), at, at,
                ),
            )
            self._event(
                conn, role=role, key=key, path=path,
                event="manual_unseal", at=at, detail=str(reason),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.store._close_conn(conn)
        state = self.get_state(role, project)
        assert state is not None
        return state

    def set_effort(
        self, role: str, project: str | Path, effort: str
    ) -> dict[str, Any]:
        role = self._validate_role(role)
        effort = str(effort).strip().lower()
        if effort not in REVIEW_EFFORTS:
            raise ReviewInputError(f"Review-Aufwand muss einer von {REVIEW_EFFORTS} sein")
        key = project_key(project)
        path = str(Path(project))
        at = _iso(self._now())
        conn = self.store._get_conn()
        try:
            self._begin(conn)
            conn.execute(
                "INSERT INTO taskplan_project_reviews "
                "(role, project_key, project_path, effort, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(role, project_key) DO UPDATE SET "
                "project_path=excluded.project_path, effort=excluded.effort, "
                "updated_at=excluded.updated_at",
                (role, key, path, effort, at, at),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.store._close_conn(conn)
        state = self.get_state(role, project)
        assert state is not None
        return state

    def events(self, role: str, project: str | Path) -> list[dict[str, Any]]:
        role = self._validate_role(role)
        key = project_key(project)
        conn = self.store._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, role, project_key, project_path, event, occurred_at, "
                "presentation_id, detail FROM taskplan_project_review_events "
                "WHERE role=? AND project_key=? ORDER BY id",
                (role, key),
            ).fetchall()
        finally:
            self.store._close_conn(conn)
        keys = (
            "id", "role", "project_key", "project_path", "event",
            "occurred_at", "presentation_id", "detail",
        )
        return [dict(zip(keys, row)) for row in rows]
