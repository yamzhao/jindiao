#!/usr/bin/env bash
set -euo pipefail
directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
common="$directory/_service-common.sh"
controller="$directory/ecs_host.py"
[[ -f "$common" ]] || common="$directory/../service-common.sh"
[[ -f "$controller" ]] || controller="$directory/../../scripts/deployment/ecs_host.py"
source "$common"
action="${1:-}"
[[ $# -eq 0 ]] || shift
case "$action" in start|restart|stop) ;; *) fail "Expected start, restart or stop." ;; esac
config="$directory/config.json"
dry_run=false
maintenance=false
wait_timeout=180
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) [[ $# -ge 2 ]] || fail "--config requires a path"; config="$2"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --maintenance-confirmed) maintenance=true; shift ;;
    --wait-timeout) [[ $# -ge 2 ]] || fail "--wait-timeout requires seconds"; wait_timeout="$2"; shift 2 ;;
    -h|--help)
      printf '%s\n' "usage: start.sh|restart.sh|stop.sh [--config PATH] [--dry-run] [--wait-timeout SECONDS]" \
        "restart/stop require --maintenance-confirmed. Existing containers only; no build or volume deletion."
      exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done
[[ "$wait_timeout" =~ ^[0-9]{1,3}$ ]] && ((10#$wait_timeout >= 1 && 10#$wait_timeout <= 600)) || fail "Invalid wait timeout."
wait_timeout=$((10#$wait_timeout))
if [[ "$dry_run" == false && "$action" != start && "$maintenance" == false ]]; then
  fail "--maintenance-confirmed is required; stop new requests and wait for current Runs first."
fi
# Python is used only to validate/read the existing JSON schema, never to operate the service.
configuration="$("${JINDIAO_ECS_PYTHON:-python3}" "$controller" service-config --config "$config")"
fields=()
while IFS= read -r field; do fields+=("$field"); done <<< "$configuration"
[[ ${#fields[@]} -eq 4 ]] || fail "Invalid service configuration."
deployment_root="${fields[0]}"
docker_cmd=("${fields[1]}" --host "${fields[2]}")
container="${fields[3]}"
if [[ "$dry_run" == true ]]; then
  printf '%s\n' "DRY RUN: ECS $action; container=$container; socket=${fields[2]}; no service changes."
  exit 0
fi
[[ -d "$deployment_root" ]] || fail "Existing deployment root is missing."
[[ ! -L "$deployment_root/deployment.lock" ]] || fail "Refusing symlinked deployment lock."
command -v flock >/dev/null || fail "The ECS lifecycle scripts require util-linux flock."
exec 9>>"$deployment_root/deployment.lock"
flock -n 9 || fail "Another ECS deployment/lifecycle operation holds the lock."
listing="$(docker_call container ls -a --filter "name=$container" --format '{{.ID}} {{.Names}}')"
identifier=""
while read -r candidate name; do
  if [[ "$name" == "$container" ]]; then
    [[ -z "$identifier" ]] || fail "Ambiguous container target."
    identifier="$candidate"
  fi
done <<< "$listing"
if [[ -z "$identifier" ]]; then
  [[ "$action" != stop ]] || { printf '%s\n' "ECS service is absent/stopped."; exit 0; }
  fail "Existing ECS container is missing; use deploy.sh to deploy first."
fi
read_container_state "$identifier"
if [[ "$action" != start && "$running" == true ]]; then
  # Read persisted Run state, not /ping (attached tasks do not change its Healthy response).
  idle_check="import json,sys; from pathlib import Path; p=Path('/app/artifacts/run-state/runs'); sys.exit(0 if p.is_dir() and all(json.loads(f.read_text()).get('status') in {'completed','partial','failed','cancelled'} for f in p.glob('*/metadata.json')) else 2)"
  docker_call exec -e PYTHONDONTWRITEBYTECODE=1 "$identifier" python -c "$idle_check" >/dev/null
fi
operate_existing "$action" "$identifier"
