#!/usr/bin/env bash
# Publish the Prompt ETL single image to a registry (GitHub Container Registry
# by default) as a multi-arch (linux/amd64 + linux/arm64) image so anyone with
# access can `docker run` it. The GHCR package inherits your repo's visibility,
# so a private repo yields a private image.
#
# Prerequisites (one-time):
#   1. Create a GitHub Personal Access Token (classic) with the `write:packages`
#      scope: https://github.com/settings/tokens
#   2. Log in to GHCR:
#        echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
#
# Usage:
#   NAMESPACE=nirmalk960 ./deploy/publish.sh                 # tags :latest
#   NAMESPACE=nirmalk960 VERSION=0.1.0 ./deploy/publish.sh   # tags :0.1.0 and :latest
#   REGISTRY=docker.io NAMESPACE=youruser ./deploy/publish.sh
#
set -euo pipefail

REGISTRY="${REGISTRY:-ghcr.io}"
NAMESPACE="${NAMESPACE:-}"
IMAGE_NAME="${IMAGE_NAME:-prompt-etl}"
VERSION="${VERSION:-}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

[ -n "$NAMESPACE" ] || die "Set NAMESPACE to your registry user/org, e.g. NAMESPACE=nirmalk960 ./deploy/publish.sh"

# Full image reference. Docker Hub omits the docker.io/ prefix in pull commands;
# GHCR and other registries keep it. GHCR requires a lowercase namespace.
NAMESPACE="$(printf '%s' "$NAMESPACE" | tr '[:upper:]' '[:lower:]')"
if [ "$REGISTRY" = "docker.io" ]; then
  REPO="$NAMESPACE/$IMAGE_NAME"
else
  REPO="$REGISTRY/$NAMESPACE/$IMAGE_NAME"
fi

# Tag list: always :latest, plus :$VERSION (and major.minor) when provided.
TAGS=(-t "$REPO:latest")
if [ -n "$VERSION" ]; then
  TAGS+=(-t "$REPO:$VERSION")
  MINOR="${VERSION%.*}"
  [ "$MINOR" != "$VERSION" ] && TAGS+=(-t "$REPO:$MINOR")
fi

bold "Publishing $REPO (platforms: $PLATFORMS)"
printf '  tags:'; printf ' %s' "${TAGS[@]//-t/}"; echo

# Confirm we can talk to the registry.
docker info >/dev/null 2>&1 || die "Docker daemon is not running."

# A buildx builder that supports multi-arch (docker-container driver).
if ! docker buildx inspect prompt-etl-builder >/dev/null 2>&1; then
  bold "Creating buildx builder 'prompt-etl-builder'..."
  docker buildx create --name prompt-etl-builder --driver docker-container --use >/dev/null
else
  docker buildx use prompt-etl-builder
fi
docker buildx inspect --bootstrap >/dev/null

bold "Building and pushing..."
docker buildx build \
  -f "$REPO_ROOT/deploy/Dockerfile.allinone" \
  --platform "$PLATFORMS" \
  "${TAGS[@]}" \
  --push \
  "$REPO_ROOT"

echo
bold "Published. Anyone can now run it with:"
cat <<EOF

  docker run -d --name prompt-etl \\
    -p 8080:8080 -v prompt-etl-data:/data \\
    --add-host=host.docker.internal:host-gateway \\
    $REPO:latest

  # then open http://localhost:8080
EOF
