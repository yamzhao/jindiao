#!/usr/bin/env bash
# Shared shell-only Docker lifecycle primitives. Callers define docker_cmd and wait_timeout.
fail() { printf '%s\n' "$*" >&2; exit 1; }

docker_call() {
  "${docker_cmd[@]}" "$@" 2>/dev/null || fail "Docker operation failed; inspect the service before retrying."
}

read_container_state() {
  local state
  state="$(docker_call inspect --format '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1")"
  read -r running health <<< "$state" || fail "Unable to read container state."
  [[ "$running" == true || "$running" == false ]] || fail "Invalid container state."
}

wait_healthy() {
  local deadline=$((SECONDS + wait_timeout))
  while (( SECONDS < deadline )); do
    read_container_state "$1"
    [[ "$running" == true ]] || fail "Container exited before becoming healthy."
    case "$health" in
      healthy) return 0 ;;
      unhealthy|none) fail "Container is unhealthy or has no health check." ;;
    esac
    sleep 1
  done
  fail "Timed out waiting for container health."
}

operate_existing() {
  local action="$1" identifier="$2"
  [[ "$identifier" =~ ^[a-f0-9]+$ ]] || fail "Invalid container identifier."
  read_container_state "$identifier"
  case "$action" in
    start)
      if [[ "$running" == false ]]; then docker_call start "$identifier" >/dev/null; fi
      wait_healthy "$identifier"
      ;;
    restart)
      docker_call restart --time 30 "$identifier" >/dev/null
      wait_healthy "$identifier"
      ;;
    stop)
      if [[ "$running" == true ]]; then docker_call stop --time 30 "$identifier" >/dev/null; fi
      read_container_state "$identifier"
      [[ "$running" == false ]] || fail "Container is still running."
      ;;
  esac
  printf '%s\n' "$action completed; container and data volumes retained."
}
