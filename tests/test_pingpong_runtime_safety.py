"""Safety regressions at the packaged runtime's public CLI and path boundary."""
import argparse
import hashlib
from datetime import datetime, timezone

import pytest

from taskplan.resources.pingpong.scripts import pingpong_runtime as runtime

UNSAFE_SLOTS = (
    "../../outside", "..\\..\\outside", "/outside", "C:\\outside",
    ".", "..", "a-to-b", "bad/host", "bad\\host", "bad\nname", "NUL",
)


@pytest.mark.parametrize("slot", UNSAFE_SLOTS)
def test_direct_state_cli_rejects_unsafe_slots_without_writes(tmp_path, capsys, slot):
    root = tmp_path / "sync"
    if slot in {"/outside", "C:\\outside"}:
        # A regressed implementation must still touch only this owned fixture.
        slot = str(tmp_path / "outside")
    args = runtime.create_parser().parse_args([
        "state", "--sync-root", str(root), "--slot", slot, "--activate",
    ])
    assert args.handler(args) == 3
    assert not root.exists()
    assert not list(tmp_path.rglob("*.state.json"))
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize("slot", UNSAFE_SLOTS)
def test_state_path_and_call_path_enforce_validation_for_direct_callers(tmp_path, slot):
    with pytest.raises(ValueError):
        runtime.state_path(tmp_path, slot)
    with pytest.raises(ValueError):
        runtime.call_path(tmp_path, slot, "peer")
    with pytest.raises(ValueError):
        runtime.call_path(tmp_path, "own", slot)


def test_environment_slot_uses_same_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGPONG_SLOT", "../../outside")
    args = runtime.create_parser().parse_args([
        "state", "--sync-root", str(tmp_path / "sync"), "--activate",
    ])
    assert args.handler(args) == 3
    assert not list(tmp_path.rglob("*.state.json"))


def test_valid_slot_keeps_additive_state_and_broadcast_call(tmp_path):
    root = tmp_path / "sync"
    path = runtime.set_state(root, "own-host.1", True, "fixture@HOST")
    assert path.parent == root / "agents" / "registry"
    original = runtime.read_json(path)
    original["unrelated"] = "preserve"
    runtime.write_json(path, original)
    runtime.set_state(root, "own-host.1", False, None)
    assert runtime.read_json(path)["unrelated"] == "preserve"
    assert runtime.call_path(root, "own-host.1", "all").parent == root / "agents"


def test_safe_state_call_check_end_handshake_is_preserved(tmp_path, capsys):
    root = tmp_path / "sync"
    runtime.set_state(root, "own", True, None)
    runtime.set_state(root, "peer", True, None)
    parser = runtime.create_parser()
    call = parser.parse_args([
        "call", "--sync-root", str(root), "--slot", "peer", "--to", "own",
        "--ttl", "10m", "--reason", "fixture",
    ])
    assert call.handler(call) == 0
    check = parser.parse_args([
        "check", "--sync-root", str(root), "--slot", "own", "--json",
    ])
    assert check.handler(check) == 0
    assert '"verdict": "call-for-me"' in capsys.readouterr().out
    call.end = True
    assert call.handler(call) == 0
    assert check.handler(check) == 1


@pytest.mark.parametrize("link_kind", ("state", "registry"))
def test_state_write_refuses_symlink_escape(tmp_path, link_kind):
    root = tmp_path / "sync"
    outside = tmp_path / "outside"
    outside.mkdir()
    state = outside / "own.state.json"
    state.write_text('{"foreign": true}', encoding="utf-8")
    registry = root / "agents" / "registry"
    try:
        if link_kind == "registry":
            registry.parent.mkdir(parents=True)
            registry.symlink_to(outside, target_is_directory=True)
        else:
            registry.mkdir(parents=True)
            (registry / "own.state.json").symlink_to(state)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(ValueError):
        runtime.set_state(root, "own", True, None)
    assert state.read_text(encoding="utf-8") == '{"foreign": true}'


def test_claude_listener_prompt_holds_without_claiming_existing_loop(tmp_path):
    args = argparse.Namespace(provider="claude", duration="15m", slot="own",
                              sync_root=str(tmp_path), host="HOST")
    with pytest.raises(ValueError, match="HOLD.*Claude.*loop"):
        runtime.build_prompt(args)


def test_oversized_duration_is_a_usage_error():
    with pytest.raises(argparse.ArgumentTypeError):
        runtime.parse_expiry("999999999999999999999d", datetime.now(timezone.utc))


def test_atomic_write_does_not_follow_predictable_temporary_symlink(tmp_path):
    root = tmp_path / "sync"
    registry = root / "agents" / "registry"
    registry.mkdir(parents=True)
    foreign = tmp_path / "foreign.json"
    foreign.write_text("unchanged", encoding="utf-8")
    try:
        (registry / "own.state.json.tmp").symlink_to(foreign)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    runtime.set_state(root, "own", True, None)
    assert foreign.read_text(encoding="utf-8") == "unchanged"
    assert runtime.read_json(registry / "own.state.json")["pingpong"]["active"] is True


def test_call_refuses_same_channel_peer_symlink_without_changing_peer(tmp_path):
    root = tmp_path / "sync"
    channel = root / "agents"
    channel.mkdir(parents=True)
    peer = channel / "call-peer-to-all.txt"
    peer.write_text("FOREIGN-SENTINEL", encoding="utf-8")
    before = hashlib.sha256(peer.read_bytes()).hexdigest()
    own = channel / "call-own-to-all.txt"
    try:
        own.symlink_to(peer)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    args = runtime.create_parser().parse_args([
        "call", "--sync-root", str(root), "--slot", "own", "--to", "all",
        "--reason", "own delta",
    ])
    assert args.handler(args) == 3
    assert hashlib.sha256(peer.read_bytes()).hexdigest() == before
    assert own.is_symlink()
    own.unlink()
    assert args.handler(args) == 0
    assert "own delta" in own.read_text(encoding="utf-8")
    assert hashlib.sha256(peer.read_bytes()).hexdigest() == before


def test_atomic_call_write_does_not_follow_a_link_swapped_after_validation(tmp_path, monkeypatch):
    root = tmp_path / "sync"
    channel = root / "agents"
    channel.mkdir(parents=True)
    peer = channel / "call-peer-to-all.txt"
    peer.write_text("FOREIGN-SENTINEL", encoding="utf-8")
    own = channel / "call-own-to-all.txt"
    original = runtime._write_text_atomic

    def swap_then_write(path, content):
        try:
            own.symlink_to(peer)
        except OSError as exc:
            pytest.skip(f"symlink creation unavailable: {exc}")
        original(path, content)

    monkeypatch.setattr(runtime, "_write_text_atomic", swap_then_write)
    args = runtime.create_parser().parse_args([
        "call", "--sync-root", str(root), "--slot", "own", "--to", "all",
        "--reason", "own delta",
    ])
    assert args.handler(args) == 0
    assert not own.is_symlink()
    assert peer.read_text(encoding="utf-8") == "FOREIGN-SENTINEL"
