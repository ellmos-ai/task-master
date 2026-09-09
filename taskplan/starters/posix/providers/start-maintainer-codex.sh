#!/usr/bin/env bash
# Optional role overrides live in ~/.taskplan/taskplan.toml.
# Without them Codex uses its own canonical CLI configuration.
# Optional: set TASKPLAN_WORKDIR and TASKPLAN_TRUSTED_AUTOMATION=1.
set -euo pipefail
exec python3 -m taskplan launch --role maintainer --provider codex "$@"
