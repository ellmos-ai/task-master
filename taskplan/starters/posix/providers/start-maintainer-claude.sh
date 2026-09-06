#!/usr/bin/env bash
# Configure models in ~/.taskplan/taskplan.toml.
# Optional: TASKPLAN_WORKDIR, TASKPLAN_CLAUDE_MCP_CONFIG, trusted automation.
set -euo pipefail
exec python3 -m taskplan launch --role maintainer --provider claude "$@"
