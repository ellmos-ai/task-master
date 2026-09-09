# -*- coding: utf-8 -*-
"""Provider-neutral launcher for TASKPLAN role workers.

The packaged ``START-*`` files are deliberately tiny wrappers around this
module. Provider command lines, prompt provenance, runtime lookup, model probe,
fallback chain and dry-run behaviour therefore have one tested implementation
instead of forty drifting copies.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Mapping, NamedTuple, Sequence

from .config import (
    active_roles, execution_config, label_runtime, model_choices, provider_name,
)
from .doctor import run as doctor
from .runtime import (
    DEFAULT_OPERATOR_MODE, LAUNCH_ROLES, OPERATOR, OPERATOR_MODES,
    normalize_operator_mode, normalize_role,
    runtime_profile, startup_prompt,
)
from .workflows import get_workflow_prompt_path

PROVIDERS = ("claude", "codex", "agy", "kimi")
TRUST_ENV = "TASKPLAN_TRUSTED_AUTOMATION"
OPERATOR_MODE_ENV = "TASKPLAN_OPERATOR_MODE"
WORKDIR_ENV = "TASKPLAN_WORKDIR"
DRY_RUN_ENV = "TASKPLAN_STARTER_DRY_RUN"
PROBE_ENV = "TASKPLAN_STARTER_PROBE"
CLAUDE_MCP_ENV = "TASKPLAN_CLAUDE_MCP_CONFIG"
AGY_SCHEDULE_MINUTES_ENV = "TASKPLAN_AGY_SCHEDULE_MINUTES"

PROBE_TOKEN = "TASKPLAN_PROBE_OK"
PROBE_REQUEST = f"Reply with exactly {PROBE_TOKEN} and nothing else."
DEFAULT_PROBE_TIMEOUT = 120.0

# Nur Anzeigewerte fuer die interaktive Abfrage. Der Launcher validiert sie
# bewusst nicht: eine CLI darf neue Stufen bekommen, ohne dass ein Update
# dieses Moduls den Start blockiert.
EFFORT_CHOICES = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("low", "medium", "high", "xhigh"),
    "agy": ("low", "medium", "high"),
    "kimi": ("low", "high", "max"),
}
CODEX_DEFAULT_LABEL = "Codex-Default (kein TASKPLAN-Override)"


class Candidate(NamedTuple):
    """Ein startbarer Versuch: Provider mit aufgeloestem Modell und Reasoning."""

    provider: str
    model: str
    effort: str


def normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in PROVIDERS:
        raise ValueError(
            f"Unbekannter Provider {provider!r}; erlaubt: {', '.join(PROVIDERS)}"
        )
    return normalized


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _workdir(env: Mapping[str, str]) -> Path:
    raw = env.get(WORKDIR_ENV, "").strip()
    path = Path(raw).expanduser() if raw else Path.cwd()
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ValueError(
            f"{WORKDIR_ENV} ist kein vorhandenes Verzeichnis: {resolved}"
        )
    return resolved


def _configured_runtime(key: str, provider: str) -> tuple[str, str]:
    """Modell und Reasoning aus der Konfiguration — fuer Rolle ODER Label."""
    if key in LAUNCH_ROLES:
        profile = runtime_profile(key, provider)
        return (str(profile["model"]).strip(),
                str(profile["reasoning_effort"]).strip())
    values = label_runtime(key, provider)
    return values["model"].strip(), values["reasoning_effort"].strip()


def _provider_commands(
    role: str,
    provider: str,
    *,
    env: Mapping[str, str],
    model: str = "",
    effort: str = "",
    prompt_path: Path | None = None,
    request: str = "",
    session_name: str = "",
) -> tuple[list[list[str]], Path]:
    """Alle Kommandos EINES Starts.

    Regelfall ist genau ein interaktives Kommando. Kimi im Prompt-Datei-Modus
    braucht zwei: die CLI nimmt einen freien Startauftrag nur headless
    entgegen (``--prompt``), die interaktive Sitzung setzt danach auf derselben
    Konversation auf (``--continue``).

    ``prompt_path`` gesetzt = externe Rolle: der Prompt kommt von aussen, der
    Nutzerauftrag ist ``request``, und es gibt kein ``startup_prompt``-Wrapping.
    """
    external = prompt_path is not None
    if not model or not effort:
        cfg_model, cfg_effort = _configured_runtime(role, provider)
        model = model or cfg_model
        effort = effort or cfg_effort
    model, effort = model.strip(), effort.strip()

    if provider != "codex" and not model:
        raise ValueError(
            f"Kein Modell konfiguriert: "
            f"[providers.{provider}.models] {role} = \"...\""
        )
    if provider != "codex" and not effort:
        raise ValueError(
            f"Kein Reasoning/Thinking konfiguriert: "
            f"[providers.{provider}.reasoning_effort] {role} = \"...\""
        )

    if external:
        if not request.strip():
            raise ValueError(
                "Externe Rolle ohne Nutzerauftrag: --request \"...\" fehlt."
            )
    else:
        prompt_path = get_workflow_prompt_path(role.upper())
        schedule_minutes = None
        if provider == "agy":
            raw_schedule = env.get(AGY_SCHEDULE_MINUTES_ENV, "").strip()
            if raw_schedule:
                try:
                    schedule_minutes = int(raw_schedule)
                except ValueError as exc:
                    raise ValueError(
                        f"{AGY_SCHEDULE_MINUTES_ENV} muss eine positive "
                        "Ganzzahl sein"
                    ) from exc
                if schedule_minutes <= 0:
                    raise ValueError(
                        f"{AGY_SCHEDULE_MINUTES_ENV} muss eine positive "
                        "Ganzzahl sein"
                    )
        prompt_kwargs = {"schedule_minutes": schedule_minutes}
        if role == OPERATOR:
            # Ungueltiger Modus bricht hier mit ValueError ab - vor dem Start.
            prompt_kwargs["operator_mode"] = normalize_operator_mode(
                env.get(OPERATOR_MODE_ENV, "")
            )
        request = startup_prompt(role, provider, **prompt_kwargs)

    trusted = _truthy(env.get(TRUST_ENV, ""))
    executable = shutil.which(provider)
    if provider == "agy":
        executable = shutil.which("agy")
    if not executable:
        raise ValueError(f"CLI für Provider {provider!r} wurde nicht gefunden.")

    if provider == "codex":
        developer = (
            "Read and follow the authorized role instructions in "
            f"{prompt_path}. Read the file completely; it is the canonical "
            "source for this session."
        )
        command = [executable]
        # Codex already has one canonical provider configuration under
        # ~/.codex/config.toml. TASKPLAN values are optional role-specific
        # overrides; omitting them lets the CLI inherit its own defaults
        # instead of forcing every host to maintain a duplicate config file.
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend([
                "--config", f"model_reasoning_effort={json.dumps(effort)}",
            ])
        command.extend([
            "--config", f"developer_instructions={json.dumps(developer)}",
        ])
        if trusted:
            command.extend([
                "--sandbox", "danger-full-access",
                "--ask-for-approval", "never",
            ])
        command.extend(["--cd", str(_workdir(env)), request])
        return [command], prompt_path

    request_with_path = (
        f"First read the complete authorized role prompt at {prompt_path}. "
        f"{request}"
    )
    if provider == "claude":
        command = [executable]
        if trusted:
            command.append("--dangerously-skip-permissions")
        command.extend([
            "--model", model,
            "--effort", effort,
            "--name", (session_name or role).strip().upper(),
        ])
        mcp_config = env.get(CLAUDE_MCP_ENV, "").strip()
        if mcp_config:
            command.extend(["--mcp-config", str(Path(mcp_config).expanduser())])
        command.extend([
            "--append-system-prompt-file", str(prompt_path),
            request_with_path,
        ])
        return [command], prompt_path

    if provider == "kimi":
        # Kimi Code CLI (Vertrag verifiziert gegen 0.29.2): die CLI kennt
        # keinen interaktiven Startprompt (positional ist ein Subcommand und
        # wird abgelehnt) und kein --effort-Flag. Der Rollen-Worker laeuft
        # deshalb headless (-p/--prompt); die Reasoning-Stufe kommt aus dem
        # default_effort des Modells in ~/.kimi-code/config.toml und wird hier
        # nur angezeigt, nicht uebergeben.
        base = [executable]
        if trusted:
            base.append("--yolo")
        base.extend(["--model", model])
        boot = base + ["--prompt", request_with_path]
        if not external:
            return [boot], prompt_path
        # Externe Rolle: nach dem headless-Boot uebernimmt der Mensch dieselbe
        # Konversation interaktiv weiter.
        return [boot, base + ["--continue"]], prompt_path

    command = [executable]
    if trusted:
        command.extend(["--dangerously-skip-permissions", "--mode", "accept-edits"])
    command.extend([
        "--model", model,
        "--effort", effort,
        "--add-dir", str(prompt_path.parent),
        "--prompt-interactive", request_with_path,
    ])
    return [command], prompt_path


def _provider_command(role: str, provider: str, **kwargs) -> tuple[list[str], Path]:
    """Das erste (im Regelfall einzige) Kommando eines Starts."""
    commands, prompt_path = _provider_commands(role, provider, **kwargs)
    return commands[0], prompt_path


def _probe_command(provider: str, executable: str, model: str,
                   effort: str) -> list[str]:
    """Einmaliger Print-Modus-Aufruf, der nur den Token ausgeben soll."""
    if provider == "claude":
        # Ohne MCP-Server: die Sonde prueft das Modell, nicht das Profil.
        return [executable, "--strict-mcp-config", "--model", model,
                "--effort", effort, "-p", PROBE_REQUEST]
    if provider == "codex":
        command = [executable, "exec", "--skip-git-repo-check",
                   "--sandbox", "read-only"]
        if model:
            command.extend(["--model", model])
        if effort:
            command.extend([
                "--config", f"model_reasoning_effort={json.dumps(effort)}",
            ])
        command.append(PROBE_REQUEST)
        return command
    if provider == "agy":
        return [executable, "--model", model, "--effort", effort,
                "-p", PROBE_REQUEST]
    return [executable, "--model", model, "--output-format", "text",
            "-p", PROBE_REQUEST]


def _terminate(proc: subprocess.Popen) -> None:
    """Beendet den Sondenprozess samt Kindern.

    Die Provider-CLIs sind ``.CMD``/``.EXE``-Shims mit Node-Kindern: ein
    ``kill`` auf den Shim liesse die Kinder als Waisen zurueck.
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            proc.kill()
    except OSError:  # pragma: no cover - taskkill fehlt
        proc.kill()


def probe(command: Sequence[str], timeout: float = DEFAULT_PROBE_TIMEOUT,
          *, cwd: Path | None = None) -> tuple[bool, str]:
    """Erfolgreich, sobald der Token im Ausgabestrom steht.

    Der Exit-Code taugt NICHT als Kriterium (gemessen 2026-09-06): agy druckt
    den Token und beendet sich danach nie — der Lauf endet im Kill nach dem
    Timeout. claude und codex beenden sich mit 0; bei ungueltigem Modell mit 1
    und ohne Token. Ein Timeout ist deshalb nur dann ein Fehlschlag, wenn bis
    dahin kein Token kam.
    """
    try:
        proc = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            cwd=str(cwd) if cwd else None,
        )
    except OSError as exc:
        return False, f"Sonde nicht startbar: {exc}"

    timed_out = False

    def _stop() -> None:
        nonlocal timed_out
        timed_out = True
        _terminate(proc)

    found = False
    timer = threading.Timer(timeout, _stop)
    timer.start()
    try:
        for line in proc.stdout or ():
            if PROBE_TOKEN in line:
                found = True
                break
    finally:
        timer.cancel()
        _terminate(proc)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
        if proc.stdout is not None:
            proc.stdout.close()

    if found:
        return True, f"{PROBE_TOKEN} erkannt"
    if timed_out:
        return False, f"kein {PROBE_TOKEN} innerhalb von {timeout:.0f}s"
    return False, f"Exit {proc.returncode} ohne {PROBE_TOKEN}"


def _fallback_order(provider: str) -> tuple[str, ...]:
    """Reihenfolge der Ersatz-Provider; Default ist die Paketreihenfolge."""
    configured = execution_config().get("fallback_providers")
    if not isinstance(configured, list):
        return tuple(name for name in PROVIDERS if name != provider)
    order: list[str] = []
    for raw in configured:
        try:
            name = normalize_provider(str(raw))
        except ValueError:
            # Ein Tippfehler in der Konfiguration darf keinen Start verhindern.
            continue
        if name != provider and name not in order:
            order.append(name)
    return tuple(order)


def _candidates(key: str, provider: str, *, model: str, effort: str,
                fallback: bool) -> tuple[list[Candidate], list[str]]:
    """Kandidatenkette und die sichtbaren Gruende der uebersprungenen Eintraege.

    Reihenfolge: die ausdrueckliche Wahl, danach die Provider-Defaults
    desselben Providers, danach die Defaults der Ersatz-Provider.
    """
    wanted: list[tuple[str, str, str]] = [(provider, model.strip(), effort.strip())]
    if fallback:
        if model.strip() or effort.strip():
            wanted.append((provider, "", ""))
        wanted.extend((name, "", "") for name in _fallback_order(provider))

    chain: list[Candidate] = []
    skipped: list[str] = []
    seen: set[Candidate] = set()
    unavailable: set[str] = set()
    for name, want_model, want_effort in wanted:
        if name in unavailable:
            continue
        cfg_model, cfg_effort = _configured_runtime(key, name)
        candidate = Candidate(name, want_model or cfg_model,
                              want_effort or cfg_effort)
        if candidate in seen:
            continue
        seen.add(candidate)
        if not shutil.which(name):
            unavailable.add(name)
            skipped.append(f"{name} — CLI nicht gefunden")
            continue
        if name != "codex" and not candidate.model:
            unavailable.add(name)
            skipped.append(
                f"{name} — kein Eintrag in [providers.{name}.models]"
            )
            continue
        chain.append(candidate)
    return chain, skipped


def _ask_one(ask, label: str, default: str, choices: Sequence[str],
             *, strict: bool = False) -> str:
    """Eine Frage; Enter uebernimmt den Default und liefert den leeren String.

    Auswahl per Nummer ([1], [2], ...) oder Name. strict=True: nur Werte aus
    der Liste; Unbekanntes wird bis zu dreimal neu gefragt, danach gilt der
    Default. strict=False (Modell): Freitext bleibt erlaubt.
    """
    hint = "".join(f"  [{i}] {c}" for i, c in enumerate(choices, 1))
    prompt = f"{label} [Enter = {default or 'CLI-Default'}]{hint}: "
    for _ in range(3):
        try:
            answer = str(ask(prompt) or "").strip()
        except EOFError:
            # Eine per Pipe gefuetterte Antwortliste darf kuerzer sein als die
            # Fragenliste - der Rest bleibt beim Default.
            return ""
        if not answer:
            return ""
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        lowered = answer.lower()
        if lowered in choices:
            return lowered
        if not strict:
            return answer
        print(f"[EINGABE] {answer!r} unbekannt - Nummer oder Name aus der Liste, "
              "Enter = Default.")
    print(f"[EINGABE] Dreimal unbekannt - nehme Default {default or 'CLI-Default'}.")
    return ""


def _first_available_provider() -> str:
    for name in PROVIDERS:
        if shutil.which(name):
            return name
    return ""


def _ask_runtime(key: str, provider: str, model: str, effort: str,
                 *, ask) -> tuple[str, str, str]:
    """Fragt nur, was nicht schon als Flag feststeht."""
    if not provider:
        default = provider_name("") or _first_available_provider()
        provider = normalize_provider(
            _ask_one(ask, "Anbieter", default, PROVIDERS, strict=True) or default
        )
    else:
        provider = normalize_provider(provider)
    cfg_model, cfg_effort = _configured_runtime(key, provider)
    if not model:
        model = _ask_one(
            ask, "Modell",
            cfg_model or (CODEX_DEFAULT_LABEL if provider == "codex" else ""),
            model_choices(provider),
        )
    if not effort:
        effort = _ask_one(
            ask, "Reasoning", cfg_effort, EFFORT_CHOICES.get(provider, ()),
            strict=True,
        )
    return provider, model, effort


def _display_command(command: Sequence[str]) -> str:
    """Readable dry-run output without shell-specific execution semantics."""
    return subprocess.list2cmdline(list(command))


def _probe_enabled(env: Mapping[str, str], override: bool | None) -> bool:
    if override is not None:
        return override
    raw = env.get(PROBE_ENV, "").strip()
    if raw:
        return _truthy(raw)
    return bool(execution_config().get("probe", True))


def _probe_timeout() -> float:
    try:
        value = float(execution_config().get(
            "probe_timeout_seconds", DEFAULT_PROBE_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_PROBE_TIMEOUT
    return value if value > 0 else DEFAULT_PROBE_TIMEOUT


def launch(
    role: str,
    provider: str = "",
    *,
    env: Mapping[str, str] | None = None,
    run=subprocess.run,
    model: str = "",
    effort: str = "",
    prompt_file: str = "",
    request: str = "",
    label: str = "",
    interactive: bool = False,
    fallback: bool = True,
    use_probe: bool | None = None,
    session_name: str = "",
    ask=input,
) -> int:
    """Validiert die Konfiguration und startet genau einen Worker.

    Mit ``label``/``prompt_file``/``request`` laeuft eine externe Rolle: kein
    TASKPLAN-Rollenname, kein Rollen-Gate, kein erzeugter Startauftrag — der
    mitgegebene Prompt wird aber auf demselben Weg ausgeliefert wie ein
    Rollen-Prompt.
    """
    actual_env = os.environ if env is None else dict(env)
    external = bool(label or prompt_file or request)
    prompt_path: Path | None = None

    if external:
        if not (label and prompt_file and request):
            print("[FEHLER] Externe Rolle braucht --label, --prompt-file und "
                  "--request zusammen.", file=sys.stderr)
            return 1
        key = label.strip()
        prompt_path = Path(prompt_file).expanduser().resolve()
        if not prompt_path.is_file():
            print(f"[FEHLER] Prompt-Datei nicht gefunden: {prompt_path}",
                  file=sys.stderr)
            return 1
    else:
        try:
            key = normalize_role(role)
        except ValueError as exc:
            print(f"[FEHLER] {exc}", file=sys.stderr)
            return 1
        if not active_roles().get(key, False):
            print(
                f"[{key.upper()}] Rolle ist in der Konfiguration "
                "abgeschaltet. Nichts zu tun."
            )
            return 0

    if doctor() != 0:
        print("[FEHLER] `python -m taskplan doctor` ist fehlgeschlagen.",
              file=sys.stderr)
        return 1

    try:
        operator_mode = ""
        if key == OPERATOR:
            if interactive and not actual_env.get(OPERATOR_MODE_ENV, "").strip():
                actual_env[OPERATOR_MODE_ENV] = _ask_one(
                    ask, "Modus", DEFAULT_OPERATOR_MODE, OPERATOR_MODES,
                    strict=True) or DEFAULT_OPERATOR_MODE
            operator_mode = normalize_operator_mode(
                actual_env.get(OPERATOR_MODE_ENV, ""))
        if interactive:
            provider, model, effort = _ask_runtime(
                key, provider, model, effort, ask=ask)
        if not provider:
            raise ValueError(
                "Kein Provider gewaehlt: --provider P oder --interactive."
            )
        normalized_provider = normalize_provider(provider)
        workdir = _workdir(actual_env)
        chain, skipped = _candidates(
            key, normalized_provider, model=model, effort=effort,
            fallback=fallback,
        )
    except ValueError as exc:
        print(f"[FEHLER] {exc}", file=sys.stderr)
        return 1

    for reason in skipped:
        print(f"[FALLBACK] uebersprungen: {reason}")
    if not chain:
        print("[FEHLER] Kein startbarer Kandidat uebrig.", file=sys.stderr)
        return 1

    print()
    print(f"[{key.upper()}] Arbeitsort:{workdir}")
    if key == OPERATOR:
        print(f"[{key.upper()}] Modus:     {operator_mode}")
    for index, candidate in enumerate(chain, start=1):
        print(f"[KETTE] {index}. {candidate.provider} "
              f"{candidate.model or CODEX_DEFAULT_LABEL}/"
              f"{candidate.effort or CODEX_DEFAULT_LABEL}")

    def _build(candidate: Candidate) -> tuple[list[list[str]], Path]:
        return _provider_commands(
            key, candidate.provider, env=actual_env,
            model=candidate.model, effort=candidate.effort,
            prompt_path=prompt_path, request=request,
            session_name=session_name,
        )

    if _truthy(actual_env.get(DRY_RUN_ENV, "")):
        try:
            commands, resolved_prompt = _build(chain[0])
        except ValueError as exc:
            print(f"[FEHLER] {exc}", file=sys.stderr)
            return 1
        print(f"[{key.upper()}] Prompt:    {resolved_prompt}")
        for command in commands:
            print(f"[DRY-RUN] {_display_command(command)}")
        return 0

    probing = _probe_enabled(actual_env, use_probe)
    timeout = _probe_timeout()
    failures: list[str] = []
    for candidate in chain:
        try:
            commands, resolved_prompt = _build(candidate)
        except ValueError as exc:
            failures.append(f"{candidate.provider}: {exc}")
            print(f"[FALLBACK] uebersprungen: {candidate.provider} — {exc}")
            continue
        print()
        print(f"[{key.upper()}] Provider:  {candidate.provider}")
        print(f"[{key.upper()}] Modell:    "
              f"{candidate.model or CODEX_DEFAULT_LABEL}")
        print(f"[{key.upper()}] Reasoning: "
              f"{candidate.effort or CODEX_DEFAULT_LABEL}")
        print(f"[{key.upper()}] Prompt:    {resolved_prompt}")
        if probing:
            ok, reason = probe(
                _probe_command(candidate.provider, commands[0][0],
                               candidate.model, candidate.effort),
                timeout,
            )
            print(f"[SONDE] {candidate.provider} "
                  f"{candidate.model or CODEX_DEFAULT_LABEL}: {reason}")
            if not ok:
                failures.append(
                    f"{candidate.provider} "
                    f"{candidate.model or CODEX_DEFAULT_LABEL}: {reason}"
                )
                continue
        for command in commands:
            completed = run(command, cwd=workdir)
            code = int(completed.returncode)
            if code != 0:
                return code
        return 0

    print("[FEHLER] Kein Kandidat konnte gestartet werden:", file=sys.stderr)
    for line in failures:
        print(f"  - {line}", file=sys.stderr)
    return 1
