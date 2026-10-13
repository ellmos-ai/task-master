#!/usr/bin/env bash
set -euo pipefail
exec python3 -m taskplan startcenter --role pingpong "$@"
