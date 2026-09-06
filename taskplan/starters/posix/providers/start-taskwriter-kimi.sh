#!/usr/bin/env bash
# Configure models in ~/.taskplan/taskplan.toml.
# Optional: set TASKPLAN_WORKDIR and TASKPLAN_TRUSTED_AUTOMATION=1.
set -euo pipefail
exec python3 -m taskplan launch --role taskwriter --provider kimi "$@"
