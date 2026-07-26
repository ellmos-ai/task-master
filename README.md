<p align="center">
  <img src="assets/banner.svg" alt="TASKPLAN" width="100%">
</p>

# taskplan

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-245%20passed-brightgreen.svg)](tests/)
[![llms.txt](https://img.shields.io/badge/llms.txt-available-orange.svg)](llms.txt)

**Deterministic task selection for LLM agents.** Zero dependencies, stdlib only,
Python ≥ 3.10.

> [!NOTE]
> **AI / LLM Integration**: `taskplan` provides deterministic selection guards and role prompts for autonomous AI agents. For detailed system concepts and documentation overview, see [llms.txt](llms.txt).

*[Deutsche Fassung → README_de.md](README_de.md)*

Most agent task loops let the *model* decide what to work on next. That sounds
flexible, and it fails in a specific, predictable way: the model picks whatever is
most visible, cleans it up, and eventually reports *"nothing left to do"* — while the
real backlog sits one directory level below, unread.

taskplan moves that decision **out of the prompt and into code**. A deterministic
selector decides *what* comes next; the model keeps the judgment calls (*is this easy?
is it safe? did it pass?*).

---

## The rule the selector enforces

```
  Surface sweep (all roots)
    → deep dive: EASY, in one root
    → back to the surface
    → deep dive: EASY, in the next root
    → … until NO root has easy work left
    → only then: the medium pass
```

**Effort is the primary sort dimension; root rotation is only secondary.** Easy tasks
are exhausted *globally* before a medium one is touched anywhere.

That is not tidiness. Easy tasks are exactly what unblocks whoever is deep inside a
hard problem somewhere else. Clearing a small thing in project A is worth more than
going deeper in project B. That is *why* the easy/medium distinction exists at all.

## Gates that live in code, not in prose

| Effort | Meaning | Autonomous? |
|---|---|---|
| `easy` | one or few files, one project, reversible, mechanically verifiable | **always** |
| `medium` | several files in **one** project, no architectural change | only when no `easy` is left anywhere |
| `large` | architecture, cross-project, migration | **never** |
| `special` | needs domain knowledge, credentials, or an irreversible action | **never** |
| *(empty)* | unclassified | **not treated as easy** — better left alone than wrongly assumed harmless |

`scope = "central"` (shared infrastructure others build on) is never autonomous
either, regardless of effort.

When nothing is selectable, `next_bundle()` returns `None`. The loop ends as an
**honest no-op** instead of inventing work to fill itself.

---

## Quick start

```python
from taskplan import api as tasks

tasks.init(agent_id="opus")
tasks.add("Fix encoding in docs", priority="high", effort="easy",
          project_path="/repos/foo", root_id="OSS")

for t in tasks.list(effort="easy", scope="local"):
    print(f"[{t['id']}] {t['title']}")

tasks.done(1)
```

Ask the selector what to do next:

```bash
python -m taskplan next            # mode, effort, project, task IDs, permissions
python -m taskplan doctor          # which database am I actually using?
python -m taskplan projects list   # what does the loop see?
python -m taskplan projects markers
```

`next` exit codes: `0` = bundle returned · `1` = nothing to do · `2` = role disabled
· `3` = retryable selector/discovery timeout.

### Who created it, who works on it

`agent_id` used to carry three meanings at once (creator, worker, role) and was
**overwritten on assignment** — so the origin was lost the moment someone picked a
task up. Now they are separate:

```python
client.add("…")                      # sets created_by  (immutable)
client.assign(task_id, to="claude")  # sets assigned_to + delegation_status
```

Whoever takes a task writes to `assigned_to` — **never** to the field carrying the
origin.

---

## Three roles

| Role | Does | Never does |
|---|---|---|
| **TASKWRITER** | finds and formalizes tasks, **classifies effort/scope** | execute them |
| **TASKSOLVER** | works a bundle, verifies it, claims it via `assign()` | choose the project |
| **MAINTAINER** | keeps files and directories clean, curates project discovery | write or solve tasks |

The writer is upstream: **an unclassified task is an invisible task**, because the
solver refuses to guess at its size.

Prompts ship with the package (`taskplan.TASKSOLVER`, `.TASKWRITER`, `.MAINTAINER`) —
as resources, not hardcoded strings, and resolvable as real files for external
launchers:

```python
from taskplan import list_workflows, get_workflow_prompt, get_workflow_prompt_path
```

### Prompt language

All three roles exist in **English and German**. Default is English; the module is
meant to be user-neutral.

```toml
[language]
prompts = "de"        # de | en
```

Override for a single run with `TASKPLAN_LANG=de`. A missing translation falls back
to English **with a warning** — the prompt is the role's contract, and a silent
language switch would be worse than a loud one. Tests assert that every promise
survives translation, in both directions.

---

## Everything is configurable — nothing is hardcoded

See [`taskplan.example.toml`](taskplan.example.toml) for the fully commented version.

### Storage

SQLite is the recommended default, but the selector talks to a narrow `TaskStore`
protocol and **knows no SQL**. A `files` backend keeps the truth in your `TODO.md`
files — no database at all. Foreign systems plug in via entry point.

Resolution order: env `TASKPLAN_DB` → `taskplan.toml` `[storage].path` → env
`RINNSAL_DB` → `~/.taskplan/taskplan.db`.

> `python -m taskplan doctor` warns when the *active* database is empty while another
> one holds data. That silent failure mode — writing into a database nobody reads, no
> error, no warning, just no effect — is exactly what it exists to catch.

### Project discovery

Five marker categories, each switchable, combined with a real boolean expression:

```toml
[traversal.markers]
expression = "(dir_patterns AND files) OR git"   # AND / OR / NOT, parentheses
```

| # | Category | Detects |
|---|---|---|
| 1 | `dir_patterns` | patterns in the folder name |
| 2 | `files` | marker files (`CLAUDE.md` is more specific than `TODO.md`) |
| 3 | `subdirs` | marker directories (`.claude`) |
| 4 | `git` | a repository — including worktrees/submodules, where `.git` is a **file** |
| 5 | `flag_file` | an explicit marker; beats every heuristic |

The expression parser is hand-written, **not `eval`** — a config file must never
execute arbitrary code. A typo in a marker name is an **error**, not a silent
"never matches"; otherwise the loop would quietly find nothing at all.

Not enough? `discovery = "manual"` uses a hand-curated registry instead of (or
alongside) the automatic scan. The MAINTAINER keeps it up to date.

> **A trap worth knowing — measured on a real system.** Folder-name patterns are
> *dangerous* with `combine = "any"` if your intermediate levels follow the same
> convention as your projects. Categories named `CASH`, `DATA`, `CODING` match an
> uppercase pattern just like the projects beneath them — the scan stops at the
> category and never descends. Result: **46 wrong "projects" instead of 91 real
> ones.** `dir_patterns AND files` fixes it. That is why `dir_patterns` defaults to
> *off*.

### Locks — three axes, not one switch

| Action | Rule |
|---|---|
| read / analyze | **always allowed** — a lock protects against *change*, not against *knowledge* |
| create a new file | usually allowed (does not collide with work on existing files) |
| modify a file | only without a foreign lock in scope |

And crucially: **a lock in one project locks that project** — not its siblings, and
not the whole pipeline.

Different system, different lock scheme? `provider = "rules"` evaluates **nothing** —
it passes your rule files through as *text into the prompt*. Better an agent that
reads the real rule than a parser that guesses at its meaning.

### Roles, models, task sources, depth

All switchable. A disabled role **aborts cleanly on start** instead of silently
idling. `combined = true` is currently parsed and exposed as configuration, but no
bundled runner or launcher consumes it yet; it is therefore not a functional
3-in-1/2-in-1 mode. Model choice belongs in the config, not in the launcher.

### Provider-neutral runtime and Codex goals

Launchers are intentionally thin. `[execution] provider` selects a provider;
`[providers.<name>.models]` and `[providers.<name>.reasoning_effort]` select values
per role. The legacy `[models]` section remains a compatible fallback.

Codex uses `continuation = "goal"`. TASKPLAN generates an explicit user startup
prompt that authorizes a persisted goal, processes one bundle per continuation,
then asks the selector again. `empty_policy = "keep_goal"` prevents a single empty
result from being mistaken for a permanently empty queue. The generated goal
contract must call `python -m taskplan backoff ...`; that command performs the real
`idle_backoff_seconds` wait before polling again. `python -m taskplan runtime ...`
exposes the profile to any shell; `python -m taskplan startup-prompt ...` emits the
provider-specific user request. No user name, home path, or model is hardcoded in
the launcher.

Project discovery has its own `discovery_timeout_seconds` and a portable snapshot
cache under `~/.taskplan/`. The scan runs in a killable child process. A
cloud-filesystem stall returns exit `3` instead of pinning the worker forever; a goal
can perform the real backoff and retry without leaking scan threads.

---

## Tasks are not tickets

Tasks (this module) and tickets (file-based systems, IDs like `T-YYYYMMDD-NN`) are
**separate systems**. Tickets *can* become tasks, but need not. The only bridge is
`api.add_from_ticket(...)`, which creates a normal task tagged `ticket:<id>`.
taskplan never imports, mirrors, or manages tickets.

## Origin & compatibility

Third pillar of the `.MEMORY` stack — **USMC** (curated session memory) · **GARDENER**
(organic memory + cross-source index) · **TASKPLAN** (tasks). Extracted from
`rinnsal/tasks`; rinnsal imports it back through a seam with a bundled fallback.

The table name **`rinnsal_tasks` is kept deliberately**, and schema changes are
**additive only** — existing readers keep working without migration.

Statuses: `open`, `active`, `done`, `cancelled` · Priorities: `critical`, `high`,
`medium`, `low` · Efforts: `easy`, `medium`, `large`, `special` · Scopes: `local`,
`central`.

## Tests

```bash
python -m pytest tests/ -q
```

## License

MIT — Lukas Geiger
