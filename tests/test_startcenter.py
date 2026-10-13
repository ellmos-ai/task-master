from pathlib import Path
from unittest import mock

from taskplan import startcenter


def test_builtin_dry_run_uses_existing_launcher_and_preserves_trust(monkeypatch):
    monkeypatch.delenv("TASKPLAN_TRUSTED_AUTOMATION", raising=False)
    with mock.patch.object(startcenter, "launch", return_value=17) as launch:
        assert startcenter.main(["--role", "taskwriter", "--provider", "codex", "--dry-run"]) == 17
    assert launch.call_args.args == ("taskwriter", "codex")
    env = launch.call_args.kwargs["env"]
    assert env["TASKPLAN_STARTER_DRY_RUN"] == "1"
    assert "TASKPLAN_TRUSTED_AUTOMATION" not in env


def test_installed_share_prompt_resolves_through_distribution_record(tmp_path, monkeypatch):
    prompt = tmp_path / "share/system-auditor/prompts/AUDITOR.de.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("Audit", encoding="utf-8")
    entry = Path("../../../share/system-auditor/prompts/AUDITOR.de.md")
    distribution = mock.Mock(files=[entry])
    distribution.locate_file.return_value = prompt
    monkeypatch.delenv("SYSTEM_AUDITOR_ROOT", raising=False)
    with mock.patch.object(startcenter.metadata, "distribution", return_value=distribution):
        assert startcenter.module_prompt("system-auditor", "de") == prompt


def test_missing_module_does_not_launch(monkeypatch):
    monkeypatch.delenv("TICKET_MASTER_ROOT", raising=False)
    with mock.patch.object(startcenter.metadata, "distribution", side_effect=startcenter.metadata.PackageNotFoundError), mock.patch.object(startcenter, "launch") as launch:
        assert startcenter.main(["--role", "ticket-master", "--dry-run"]) == 2
    launch.assert_not_called()


def test_explicit_module_root_supports_spaces_and_german_prompt(tmp_path, monkeypatch):
    root = tmp_path / "Nutzer mit Leerzeichen ä"
    prompt = root / "prompts/TICKET-MASTER.de.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("Prüfe", encoding="utf-8")
    monkeypatch.setenv("TICKET_MASTER_ROOT", str(root))
    with mock.patch.object(startcenter, "launch", return_value=0) as launch:
        assert startcenter.main(["--role", "ticket-master", "--provider", "codex", "--dry-run"]) == 0
    assert launch.call_args.kwargs["prompt_file"] == str(prompt)
    assert "(a) through (d)" in launch.call_args.kwargs["request"]


def test_pingpong_requires_explicit_safe_slot_and_sync_root(monkeypatch):
    monkeypatch.delenv("PINGPONG_SLOT", raising=False)
    monkeypatch.delenv("PINGPONG_SYNC_ROOT", raising=False)
    with mock.patch.object(startcenter, "launch") as launch:
        for extra in ([], ["--slot", "../foreign", "--sync-root", "."], ["--slot", "mine"], ["--slot", "a-to-b", "--sync-root", "."]):
            assert startcenter.main(["--role", "pingpong", "--provider", "codex", "--dry-run", *extra]) == 2
    launch.assert_not_called()


def test_pingpong_listener_builds_absolute_expiry_and_disables_fallback(tmp_path):
    with mock.patch.object(startcenter, "launch", return_value=0) as launch:
        assert startcenter.main(["--role", "pingpong", "--provider", "codex", "--slot", "own-host", "--sync-root", str(tmp_path), "--duration", "15m", "--dry-run"]) == 0
    kwargs = launch.call_args.kwargs
    assert "Expiry:" in kwargs["request"]
    assert "persisted goal" in kwargs["request"]
    assert kwargs["fallback"] is False and kwargs["no_clutch"] is True
    assert Path(kwargs["prompt_file"]).is_file()


def test_pingpong_write_mode_never_requests_listener(tmp_path):
    with mock.patch.object(startcenter, "launch", return_value=0) as launch:
        assert startcenter.main(["--role", "pingpong", "--provider", "claude", "--slot", "own", "--sync-root", str(tmp_path), "--mode", "WriteSync", "--dry-run"]) == 0
    assert "do not start a listener or scheduler" in launch.call_args.kwargs["request"]
    assert "persisted goal" not in launch.call_args.kwargs["request"]


def test_unsupported_pingpong_adapter_fails_before_launch():
    with mock.patch.object(startcenter, "launch") as launch:
        assert startcenter.main(["--role", "pingpong", "--provider", "agy", "--dry-run"]) == 2
    launch.assert_not_called()


def test_multibot_delegates_to_public_core_with_environment_dry_run(monkeypatch):
    monkeypatch.setenv("TASKPLAN_STARTER_DRY_RUN", "1")
    with mock.patch.object(startcenter.util, "find_spec", return_value=object()), mock.patch.object(startcenter.subprocess, "call", return_value=23) as run:
        assert startcenter.main(["--role", "multi-bot", "--mode", "triage", "--provider", "codex"]) == 23
    assert run.call_args.args[0][1:] == ["-m", "githubbot_core", "multi-bot", "--provider", "codex", "--mode", "triage", "--dry-run"]


def test_bad_menu_choice_starts_nothing():
    with mock.patch("builtins.input", return_value="99"), mock.patch.object(startcenter, "launch") as launch:
        assert startcenter.main([]) == 2
    launch.assert_not_called()
