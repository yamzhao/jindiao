#!/usr/bin/env bash
set -euo pipefail
directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
controller="$directory/ecs_host.py"
# Repository fallback supports --help; apply requires an extracted release package.
if [[ ! -f "$controller" ]]; then
  controller="$directory/../../scripts/deployment/ecs_host.py"
fi
exec "${JINDIAO_ECS_PYTHON:-python3}" "$controller" deploy "$@"
