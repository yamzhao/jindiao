#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root/deploy/service-common.sh"
action=start
if [[ $# -gt 0 && "$1" != -* ]]; then action="$1"; shift; fi
[[ "$action" != up ]] || action=start
case "$action" in start|restart|stop|status|logs) ;; *) fail "Unknown local action: $action" ;; esac
env_file="$root/.env"
explicit_env=false
mock=false
port=8080
wait_timeout=180
dry_run=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) [[ $# -ge 2 ]] || fail "--env-file requires a path"; env_file="$2"; explicit_env=true; shift 2 ;;
    --mock) mock=true; shift ;;
    --port) [[ $# -ge 2 ]] || fail "--port requires a number"; port="$2"; shift 2 ;;
    --wait-timeout) [[ $# -ge 2 ]] || fail "--wait-timeout requires seconds"; wait_timeout="$2"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help)
      printf '%s\n' "usage: start.sh|restart.sh|stop.sh [--dry-run] [--wait-timeout SECONDS]" \
        "start.sh defaults to real formal+tianyancha using .env; --mock explicitly selects offline fixtures." \
        "start.sh also accepts --env-file PATH and --port PORT. bin/local retains up/status/logs." \
        "restart/stop target the existing jindiao-local service; they do not rebuild or delete data."
      exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done
[[ "$port" =~ ^[0-9]{1,5}$ ]] && ((10#$port >= 1024 && 10#$port <= 65535)) || fail "Invalid local port."
[[ "$wait_timeout" =~ ^[0-9]{1,3}$ ]] && ((10#$wait_timeout >= 1 && 10#$wait_timeout <= 600)) || fail "Invalid wait timeout."
port=$((10#$port))
wait_timeout=$((10#$wait_timeout))
if [[ "$mock" == true && "$explicit_env" == false ]]; then env_file="$root/deploy/local/mock.env.example"; fi
profile=live
[[ "$mock" == false ]] || profile=mock
[[ "$env_file" == /* ]] || env_file="$PWD/$env_file"
if [[ "$dry_run" == true ]]; then
  printf '%s\n' "DRY RUN: local $action; start-profile=$profile; project=jindiao-local; loopback=127.0.0.1:$port" \
    "start uses $env_file; restart/stop preserve the existing container configuration and volumes."
  exit 0
fi
docker_cmd=(docker)
endpoint="${DOCKER_HOST:-}"
if [[ -n "${DOCKER_CONTEXT:-}" || -z "$endpoint" ]]; then
  context="$(docker_call context show)"
  endpoint="$(docker_call context inspect "$context" --format '{{.Endpoints.docker.Host}}')"
fi
[[ "$endpoint" == unix:///* ]] || fail "Local service scripts require a local Unix Docker socket."

# Only this project's lifecycle is serialized; no process-name killing or global Docker cleanup.
if [[ "$action" != status && "$action" != logs ]]; then
  mkdir -p "$root/artifacts"
  lock_dir="$root/artifacts/.local-service.lock"
  mkdir "$lock_dir" 2>/dev/null || fail "Another local lifecycle operation is running (or its lock needs inspection)."
  trap 'rmdir -- "$lock_dir" 2>/dev/null || true' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
fi

if [[ "$action" == start ]]; then
  [[ -f "$env_file" ]] || fail "Runtime env file missing; specify an existing --env-file."
  # Remove ambient business/Compose overrides without printing or evaluating dotenv content.
  while IFS= read -r variable; do
    case "$variable" in JINDIAO_*|MODEL_*|TIANYANCHA_*|COMPOSE_*) unset "$variable" ;; esac
  done < <(compgen -e)
  export JINDIAO_BIND_HOST=127.0.0.1 JINDIAO_PORT="$port"
  export JINDIAO_ARTIFACT_MOUNT=local-artifacts JINDIAO_ENV_FILE="$env_file"
  docker_call image inspect jindiao:local >/dev/null
  compose_args=(compose --project-name jindiao-local --project-directory "$root" --env-file "$env_file" \
    -f "$root/compose.yaml" -f "$root/deploy/local/compose.$profile.yaml" \
    -f "$root/deploy/local/compose.local-deps.yaml")
  "${docker_cmd[@]}" "${compose_args[@]}" config --quiet >/dev/null 2>&1 || \
    fail "Invalid configuration: real mode requires MODEL_PROVIDER, MODEL_NAME, MODEL_BASE_URL, MODEL_API_KEY and TIANYANCHA_MCP_AUTHORIZATION. No Mock fallback."
  docker_call "${compose_args[@]}" up -d --build --wait --wait-timeout "$wait_timeout" >/dev/null
  printf '%s\n' "Healthy: http://127.0.0.1:$port/ping (process health, not live-business acceptance)."
  exit 0
fi

listing="$(docker_call container ls -a --filter label=com.docker.compose.project=jindiao-local \
  --filter label=com.docker.compose.service=jindiao --filter label=com.docker.compose.oneoff=False --format '{{.ID}}')"
identifiers=()
while IFS= read -r identifier; do
  [[ -z "$identifier" ]] || identifiers+=("$identifier")
done <<< "$listing"
if [[ ${#identifiers[@]} -eq 0 ]]; then
  if [[ "$action" == stop || "$action" == status ]]; then printf '%s\n' "Local service is absent/stopped."; exit 0; fi
  fail "No local container exists; run bin/start.sh first."
fi
[[ ${#identifiers[@]} -eq 1 ]] || fail "Expected one local service container; refusing ambiguous targets."
identifier="${identifiers[0]}"
case "$action" in
  status) docker_call inspect --format '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$identifier" ;;
  logs) docker_call logs --tail 100 "$identifier" ;;
  *) operate_existing "$action" "$identifier" ;;
esac
