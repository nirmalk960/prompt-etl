#!/usr/bin/env bash
# Manage a throwaway Prompt ETL container for testing, isolated from any
# real install (own name, port, and volume).
#
# Usage:
#   start_test_container.sh up [image]     # fresh container + empty volume
#   start_test_container.sh restart        # restart, keeping the volume
#   start_test_container.sh down           # remove container and volume
#   start_test_container.sh logs           # tail container logs
#
# Env: PORT (default 8090), NAME (prompt-etl-test), VOLUME (prompt-etl-test-data),
#      OLLAMA_MODEL (passed through when set), OLLAMA_DISABLE (1 to force stub codegen)
set -euo pipefail

CMD="${1:-up}"
IMAGE="${2:-ghcr.io/nirmalk960/prompt-etl:latest}"
PORT="${PORT:-8090}"
NAME="${NAME:-prompt-etl-test}"
VOLUME="${VOLUME:-prompt-etl-test-data}"
API="http://localhost:$PORT/api"

wait_ready() {
  for i in $(seq 1 90); do
    if curl -fsS "$API/ready" >/dev/null 2>&1; then
      echo "ready after ~$((i * 2))s: $(curl -fsS "$API/ready")"
      echo "API=$API"
      return 0
    fi
    sleep 2
  done
  echo "container not ready after 180s; last logs:" >&2
  docker logs --tail 60 "$NAME" >&2 || true
  return 1
}

case "$CMD" in
  up)
    docker info >/dev/null 2>&1 || { echo "Docker daemon is not running" >&2; exit 1; }
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker volume rm "$VOLUME" >/dev/null 2>&1 || true
    if ! docker pull "$IMAGE"; then
      docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "pull failed and $IMAGE is not available locally" >&2; exit 1; }
      echo "pull failed; using the local copy of $IMAGE" >&2
    fi
    # The test container is not a public deployment. Dev login stays available
    # here so the matrix can keep using Bearer dev:<email>. Do not copy this
    # flag onto a server other people can reach.
    ENV_ARGS=(-e ALLOW_DEV_AUTH=true -e APP_ENV=local)
    [ -n "${OLLAMA_MODEL:-}" ] && ENV_ARGS+=(-e "OLLAMA_MODEL=$OLLAMA_MODEL")
    [ -n "${OLLAMA_DISABLE:-}" ] && ENV_ARGS+=(-e "OLLAMA_DISABLE=$OLLAMA_DISABLE")
    docker run -d --name "$NAME" -p "$PORT:8080" -v "$VOLUME:/data" \
      --add-host=host.docker.internal:host-gateway \
      ${ENV_ARGS[@]+"${ENV_ARGS[@]}"} "$IMAGE" >/dev/null
    wait_ready
    ;;
  restart)
    docker restart "$NAME" >/dev/null
    wait_ready
    ;;
  down)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker volume rm "$VOLUME" >/dev/null 2>&1 || true
    echo "removed $NAME and $VOLUME"
    ;;
  logs)
    docker logs --tail 200 "$NAME"
    ;;
  *)
    echo "unknown command: $CMD (use up|restart|down|logs)" >&2
    exit 2
    ;;
esac
