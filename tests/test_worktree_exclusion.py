# -*- coding: utf-8 -*-
"""T-20260920-535056160: Worktrees sind keine Projekte, Zeitlimit hat einen Ausgang."""
from pathlib import Path

from taskplan.markers import MarkerRules, is_git_worktree
from taskplan.readiness import initialize


def _make(tmp_path: Path, name: str, gitdir: str) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "CLAUDE.md").write_text("x", encoding="utf-8")
    (d / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    return d


def test_worktree_wird_nicht_als_projekt_gezaehlt(tmp_path):
    wt = _make(tmp_path, "bach-wave1", "C:/_Local_DEV/repos/bach/.git/worktrees/bach-wave1")
    sub = _make(tmp_path, "vendor", "C:/repo/.git/modules/vendor")
    klon = tmp_path / "bach"
    klon.mkdir()
    (klon / "CLAUDE.md").write_text("x", encoding="utf-8")
    (klon / ".git").mkdir()

    rules = MarkerRules()
    assert is_git_worktree(wt) is True
    assert rules.matches(wt) is False        # Worktree: raus
    assert rules.matches(sub) is True        # Submodul: bleibt
    assert rules.matches(klon) is True       # Hauptklon: bleibt


def test_flagdatei_schlaegt_das_worktree_veto(tmp_path):
    wt = _make(tmp_path, "wt", "/repo/.git/worktrees/wt")
    (wt / ".taskplan-project").write_text("", encoding="utf-8")
    assert MarkerRules().matches(wt) is True


class _Store:
    """Minimal-Store: initialize() braucht nur _get_conn/_close_conn."""

    def __init__(self, tmp_path):
        import sqlite3
        self._conn = sqlite3.connect(tmp_path / "t.db")

    def _get_conn(self):
        return self._conn

    def _close_conn(self, conn):
        pass


def test_skip_timeouts_oeffnet_das_gate_und_benennt_die_projekte(tmp_path, monkeypatch):
    import taskplan.readiness as readiness

    projekt = tmp_path / "langsam"
    projekt.mkdir()

    def _timeout(path, exclude, cached, timeout, progress=None):
        raise readiness.ProjectInitializationTimeout(Path(path), "digest_records", 30.0)

    monkeypatch.setattr(readiness, "_bounded_project_digest", _timeout)
    store = _Store(tmp_path)

    gesperrt = initialize(store, [projekt])
    assert gesperrt["ready"] is False
    assert "--skip-timeouts" in gesperrt["reason"]

    offen = initialize(store, [projekt], skip_timeouts=True)
    assert offen.get("ready") is not False
    assert offen["skipped_timeouts"] == [str(projekt)]


def test_worktree_unterbaum_wird_nicht_betreten(tmp_path):
    """Nur 'kein Projekt' genuegt nicht -- sonst gilt der Unterordner als Projekt."""
    from taskplan.traversal import TraversalConfig, find_projects

    root = tmp_path / "worktrees"
    wt = root / "bach-pr7"
    (wt / "system").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /repo/.git/worktrees/bach-pr7\n", encoding="utf-8")
    (wt / "system" / "CLAUDE.md").write_text("x", encoding="utf-8")

    config = TraversalConfig(roots=[root], max_depth=3, markers=("CLAUDE.md",))
    assert find_projects(config) == []

    # und die Root selbst ist per skip_dirs ausschliessbar
    echt = root / "klon"
    echt.mkdir()
    (echt / "CLAUDE.md").write_text("x", encoding="utf-8")
    assert len(find_projects(config)) == 1
    config_ohne = TraversalConfig(roots=[root], max_depth=3, markers=("CLAUDE.md",),
                                  skip_dirs=("worktrees",))
    assert find_projects(config_ohne) == []
