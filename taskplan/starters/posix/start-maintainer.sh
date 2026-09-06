#!/usr/bin/env bash
# Provider-neutral: asks for provider, model and reasoning at start.
# Configure defaults in ~/.taskplan/taskplan.toml.
# Optional: TASKPLAN_WORKDIR, TASKPLAN_TRUSTED_AUTOMATION, TASKPLAN_STARTER_PROBE=0.
# One provider per file lives in the providers subfolder.
set -euo pipefail
exec python3 -m taskplan launch --role maintainer --interactive "$@"
