#!/bin/bash
# Build and push a Docker image to a registry.
# Usage: ./scripts/deploy/build_push.sh [tag]  (default: latest)
#
# Reads from env or .env.deploy:
#   APP_IMAGE    e.g. ghcr.io/beejones/protected-container:latest
#   REGISTRY     e.g. ghcr.io
#   IMAGE_NAME   e.g. beejones/my-app
#   DOCKERFILE   e.g. docker/Dockerfile (default)

set -euo pipefail

if [ -f .env.deploy ]; then
  set -a
  # shellcheck disable=SC1091
  . .env.deploy
  set +a
fi

REGISTRY="${REGISTRY:-ghcr.io}"
DOCKERFILE="${DOCKERFILE:-docker/Dockerfile}"
TAG="${1:-latest}"

APP_IMAGE="${APP_IMAGE:-}"
if [ -n "$APP_IMAGE" ]; then
  FULL_IMAGE="$APP_IMAGE"
else
  IMAGE_NAME="${IMAGE_NAME:?IMAGE_NAME must be set when APP_IMAGE is not set}"
  FULL_IMAGE="$REGISTRY/$IMAGE_NAME:$TAG"
fi

echo "[build-push] Building $FULL_IMAGE..."
docker build -f "$DOCKERFILE" -t "$FULL_IMAGE" .

echo "[build-push] Pushing $FULL_IMAGE..."
docker push "$FULL_IMAGE"

echo "[build-push] Done."
