"""Portable menu for packaged TASKPLAN and optional module roles."""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
from importlib import metadata, resources, util
from pathlib import Path

from .config import provider_name
from .launcher import DRY_RUN_ENV, launch

ROLES = (
    "tasksolver", "taskwriter", "maintainer", "operator",
    "system-auditor", "ticket-master", "pingpong", "multi-bot",
)
REQUESTS = {
    "system-auditor": "Start as SYSTEM-AUDITOR. Complete preflight steps (a0) and (a) from the role prompt, and confirm the audit scope with the user before the sweep.",
    "ticket-master": "Start as TICKET-MASTER. Complete boot steps (a) through (d) and enter POSITION 0.",
}


def module_prompt(role: str, language: str) -> Path:
    """Use an explicit source root or the distribution's installed share data."""
    stem = "AUDITOR" if role == "system-auditor" else "TICKET-MASTER"
    relative = f"prompts/{stem}.{language}.md"
    root = os.environ.get(role.upper().replace("-", "_") + "_ROOT", "").strip()
    if root:
        path = Path(root).expanduser().resolve() / relative
    else:
        try:
            distribution = metadata.distribution(role)
        except metadata.PackageNotFoundError as exc:
            raise ValueError(f"{role} fehlt. Installation: python -m pip install {role}") from exc
        suffix = f"share/{role}/{relative}"
        matches = [entry for entry in distribution.files or () if entry.as_posix().endswith(suffix)]
        if len(matches) != 1:
            raise ValueError(f"{role} enthält keinen eindeutigen Rollen-Prompt; installiere eine Version mit Starter-Ressourcen.")
        path = Path(distribution.locate_file(matches[0])).resolve()
    if not path.is_file():
        raise ValueError(f"Rollen-Prompt fehlt: {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Nutzerneutrales Agenten-Startcenter")
    parser.add_argument("--role", choices=ROLES)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--lang", choices=("de", "en"), default="de")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--effort", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-probe", action="store_true")
    parser.add_argument("--no-fallback", action="store_true")
    parser.add_argument("--no-clutch", action="store_true")
    parser.add_argument("--duration", default="24h")
    parser.add_argument("--sync-root", default="")
    parser.add_argument("--slot", default="")
    parser.add_argument("--mode", choices=("ListenSync", "WriteSync", "triage", "review-merge", "aftercare", "1", "2", "3"))
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(ROLES))
        return 0
    role = args.role
    if not role:
        for index, name in enumerate(ROLES, 1):
            print(f"[{index}] {name.upper()}")
        print("[Q] Beenden")
        choice = input("Auswahl: ").strip().lower()
        if choice == "q":
            return 0
        if choice.isdigit() and 1 <= int(choice) <= len(ROLES):
            role = ROLES[int(choice) - 1]
        elif choice in ROLES:
            role = choice
        else:
            print("[FEHLER] Ungültige Auswahl.", file=sys.stderr)
            return 2
    if role == "multi-bot":
        if util.find_spec("githubbot_core") is None:
            print("[FEHLER] githubbot-core fehlt. Installation: python -m pip install githubbot-core", file=sys.stderr)
            return 2
        command = [sys.executable, "-m", "githubbot_core", "multi-bot"]
        for flag, value in (("--provider", args.provider), ("--model", args.model), ("--effort", args.effort), ("--mode", args.mode)):
            if value:
                command.extend((flag, value))
        if args.mode in {"ListenSync", "WriteSync"}:
            print("[FEHLER] MULTI-BOT-Modus muss triage, review-merge oder aftercare sein.", file=sys.stderr)
            return 2
        if args.dry_run or os.environ.get(DRY_RUN_ENV, "").lower() in {"1", "true", "yes", "on"}:
            command.append("--dry-run")
        return subprocess.call(command)
    env = dict(os.environ)
    if args.dry_run:
        env[DRY_RUN_ENV] = "1"
    kwargs = {
        "env": env, "model": args.model, "effort": args.effort,
        "interactive": not args.dry_run, "fallback": not args.no_fallback,
        "use_probe": False if args.no_probe else None,
        "session_name": args.name, "no_clutch": args.no_clutch,
    }
    try:
        if role in REQUESTS:
            if args.mode:
                raise ValueError("Diese Modulrolle hat keinen --mode-Schalter.")
            kwargs.update(label=role, prompt_file=str(module_prompt(role, args.lang)), request=REQUESTS[role])
        elif role == "pingpong":
            from .resources.pingpong.scripts.pingpong_runtime import build_prompt

            args.provider = args.provider or provider_name("")
            if not args.provider and not args.dry_run:
                args.provider = input("PingPong-Anbieter (codex oder claude): ").strip().lower()
            if args.provider not in {"codex", "claude"}:
                raise ValueError("Der mitgelieferte PingPong-Vertrag unterstützt codex und claude; wähle --provider ausdrücklich.")
            args.slot = args.slot or env.get("PINGPONG_SLOT", "")
            args.sync_root = args.sync_root or env.get("PINGPONG_SYNC_ROOT", "")
            if not args.slot or not re.fullmatch(r"[A-Za-z0-9_.-]+", args.slot) or args.slot in {".", ".."} or "-to-" in args.slot:
                raise ValueError("Ein gültiger eigener Slot fehlt: --slot oder PINGPONG_SLOT.")
            if not args.sync_root:
                raise ValueError("Sync-Root fehlt: --sync-root oder PINGPONG_SYNC_ROOT.")
            mode = args.mode or "ListenSync"
            if mode not in {"ListenSync", "WriteSync"}:
                raise ValueError("PingPong-Modus muss ListenSync oder WriteSync sein.")
            args.host = socket.gethostname()
            if mode == "ListenSync":
                request = build_prompt(args)
            else:
                request = f"Use the bundled PingPong contract in WriteSync mode. Sync root: {Path(args.sync_root).expanduser()}. Write only to own slot: {args.slot}. Send the authorized delta with FileCommander evidence; do not start a listener or scheduler."
            prompt = resources.files("taskplan.resources.pingpong").joinpath("SKILL.md" if args.lang == "de" else "SKILL.en.md")
            kwargs.update(label=role, prompt_file=str(prompt), request=request, fallback=False, no_clutch=True)
        elif args.mode:
            raise ValueError("--mode gilt für PingPong oder MULTI-BOT; Operator-Modus über TASKPLAN_OPERATOR_MODE konfigurieren.")
        return launch(role, args.provider, **kwargs)
    except (ValueError, OSError) as exc:
        print(f"[FEHLER] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
