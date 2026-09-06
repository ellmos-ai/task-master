#!/usr/bin/env bash
# Configure models in ~/.taskplan/taskplan.toml.
# Optional: TASKPLAN_WORKDIR, TASKPLAN_AGY_SCHEDULE_MINUTES, trusted automation.
set -euo pipefail
exec python3 -m taskplan launch --role maintainer --provider agy "$@"
