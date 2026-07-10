#!/usr/bin/env bash
# One-command installer for the PromptETL single-image deployment.
#
#   ./scripts/install.sh                # build + run on port 8080
#   PORT=9090 ./scripts/install.sh     # custom port
#   IMAGE=prompt-etl:v0.1 ./scripts/install.sh
#
# Works on any machine with Docker (Linux, macOS, Windows/WSL2).
set -euo pipefail

PORT="${PORT:-8080}"
IMAGE="${IMAGE:-prompt-etl:latest}"
NAME="${NAME:-prompt-etl}"
VOLUME="${VOLUME:-prompt-etl-data}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5-coder:7b}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

# 1. Docker present?
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install it from https://docs.docker.com/get-docker/"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running. Start Docker and retry."
  exit 1
fi

# 2. Build the image if it doesn't exist yet (or REBUILD=1).
if [ "${REBUILD:-0}" = "1" ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  bold "Building $IMAGE (first build takes a few minutes)..."
  docker build -f "$REPO_ROOT/deploy/Dockerfile.allinone" -t "$IMAGE" "$REPO_ROOT"
else
  bold "Image $IMAGE already exists (set REBUILD=1 to rebuild)."
fi

# 3. Replace any existing container.
if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  bold "Removing existing container $NAME..."
  docker rm -f "$NAME" >/dev/null
fi

# 4. Run.
bold "Starting $NAME on port $PORT (data volume: $VOLUME)..."
docker run -d --name "$NAME" \
  -p "$PORT:8080" \
  -v "$VOLUME:/data" \
  --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_MODEL="$OLLAMA_MODEL" \
  "$IMAGE" >/dev/null

# 5. Wait for readiness.
bold "Waiting for the app to become ready..."
for i in $(seq 1 60); do
  if curl -fsS "http://localhost:$PORT/api/ready" >/dev/null 2>&1; then
    echo
    bold "PromptETL is running:  http://localhost:$PORT"
    curl -fsS "http://localhost:$PORT/api/ready" | sed 's/^/  /'
    echo
    echo "Optional: set up the local LLM with  ./scripts/setup_ollama.sh"
    echo "Logs:     docker logs -f $NAME"
    exit 0
  fi
  sleep 2
done

echo "App did not become ready in 120s. Check logs with: docker logs $NAME"
exit 1
