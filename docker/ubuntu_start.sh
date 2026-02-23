#!/bin/bash
# Ubuntu server startup script for protected container.
# Sources .env and .env.secrets from the host directory, then starts the app.

set -e

ENV_DIR="${ENV_DIR:-/opt/app}"

echo "[ubuntu_start] Starting container..."

echo "[ubuntu_start] ENV_DIR=$ENV_DIR"

for f in "$ENV_DIR/.env" "$ENV_DIR/.env.secrets"; do
    if [ -f "$f" ]; then
        echo "[ubuntu_start] Sourcing $f"
        set -a
        # shellcheck disable=SC1090
        . "$f"
        set +a
    else
        echo "[ubuntu_start] Skipping missing $f"
    fi
done

echo "[ubuntu_start] Starting application..."
exec "$@"
