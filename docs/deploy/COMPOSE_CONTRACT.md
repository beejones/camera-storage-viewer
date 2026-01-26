# Docker Compose Deployment Contract

This document defines the expected structure of `docker-compose.yml` for successful deployment via `scripts/deploy/azure_deploy_container.py`.

## Overview

The deployment engine uses Docker Compose as the single source of truth for service configuration. It identifies container roles using the `x-deploy-role` extension field.

## Required Roles

### `app` (viewer)
The primary application container.
- **Role**: `x-deploy-role: app`
- **Port**: Must either define `ports` (e.g., `- "8081:8081"`) or expect a `WEB_PORT` environment variable.
- **Command**: Should specify the command to run (e.g., `uvicorn main:app --host 0.0.0.0 --port ${WEB_PORT:-8081}`). The engine will normalize this to an ACI command array.

### `sidecar` (caddy)
The reverse proxy container.
- **Role**: `x-deploy-role: sidecar`
- **Ports**: Typically exposes `80` and `443`.
- **Target**: Must be configured to proxy to the `app` container's internal port.

## Deployment Semantics

- **Commands**: String `command` fields are split into arrays or wrapped in `sh -lc` if interpolation is detected. List `command` and `entrypoint` fields are used as-is.
- **Environment Variables**: Variables defined in `environment` are injected into the ACI container spec.
- **Bind Mounts**: **WARNING**: Local bind mounts (e.g., `./.env:/app/.env`) are **ignored** during deployment. Only Azure Files mounts are supported for durable storage.
- **Ports**: Only publicly exposed ports (defined in the `ports` section) are mapped to the ACI container group.
