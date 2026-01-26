# Plan: Rewrite `azure_deploy_container.py` to be Compose-Driven (camera-storage-viewer)

## Why
Right now we have two competing realities:

- **Goal:** Docker Compose is the single source of truth, and repo-specific deploy customization lives in hooks.
- **Reality:** `scripts/deploy/azure_deploy_container.py` still embodies the upstream “protected-azure-container / code-server” deployment shape (e.g. `CODE_SERVER_PORT`), which can produce an ACI container group that boots Caddy but crashes the app container for camera-storage-viewer.

This plan rewrites `scripts/deploy/azure_deploy_container.py` so it **derives the deploy spec from `docker-compose.yml`**, and uses **deploy hooks** to customize behavior without forking core logic.

## Non-Goals
- Do not require Docker engine on the machine running the deploy script.
- Do not re-introduce hardcoded service names/ports in the deploy script.
- Do not break the existing viewer deployment workflow (CI + docs) during the transition.

## Constraints
- ACI container groups expose **max 5 public ports** → FTP (21 + passive range) typically consumes all 5 → “full” deployment requires **two container groups** (web+caddy group + ftp group).
- Compose has explicit roles today via `x-deploy-role`:
  - `web`: `x-deploy-role: app` with `WEB_PORT=8081` and a `command: uvicorn ...`
  - `caddy`: `x-deploy-role: sidecar` with ports `80/443`
  - `ftp`: `x-deploy-role: ftp` with ports `21` + passive range

## Success Criteria (Acceptance)
- Running `python scripts/deploy/azure_deploy_container.py ...` produces an ACI container group definition that boots the **web** container successfully for this repo.
- The script chooses app/sidecar/ftp services from `docker-compose.yml` using `x-deploy-role` first.
- Hooks remain the customization layer (default auto-load: `scripts/deploy/deploy_customizations.py`).
- No lingering `CODE_SERVER_PORT` assumptions in the viewer deployment path.
- Tests remain green and we add/adjust tests to protect the new behavior.

## High-Level Approach
Rewrite `azure_deploy_container.py` into a compose-driven engine by:

1) **Normalize Compose → DeployPlan**
   - Parse compose file via existing `scripts/deploy/docker_compose_helpers.py` (PyYAML-based, no docker runtime).
   - Identify services via `x-deploy-role` (`app`, `sidecar`, `ftp`) with clear error messages when missing/ambiguous.
   - Extract:
     - images/build context + Dockerfile path
     - container ports (app/internal, sidecar/public, ftp ports)
     - commands/entrypoint needed for app to run (e.g. `uvicorn ...`)
     - required volumes/mount targets (translate host mounts to Azure Files mounts where supported)

2) **Render ACI YAML from DeployPlan**
   - Stop hardcoding “code-server behind caddy” assumptions.
   - For `web-caddy`: generate a 2-container group (web + caddy).
   - For `ftp`: generate a 1-container group (ftp only) exposing 21 + passive.
   - For `full`: orchestrate two container groups (web-caddy group + ftp group).

3) **Hook lifecycle becomes the customization layer**
   - Maintain strict schema validation.
   - Allow `build_deploy_plan` to override images/ports/etc.
   - Later: wire in `pre_render_yaml` / `post_render_yaml` / `pre_az_apply` / `post_deploy` consistently.

## Implementation Phases

### Phase 1 — Compose Contract + Detection
- [ ] Define the compose contract in docs:
  - required roles: `app` and optionally `sidecar`, optionally `ftp`
  - required ports (from compose)
  - required app command (from compose)
- [ ] Update `azure_deploy_container.py` service detection:
  - Prefer `x-deploy-role` values
  - Provide `--compose-app-service`, `--compose-caddy-service`, `--compose-ftp-service` overrides
  - Fail fast if roles are missing/ambiguous

### Phase 2 — Correct Web Container Startup (Compose Command)
- [ ] Ensure ACI YAML includes the **app command** from compose `web.command` so the container starts uvicorn.
- [ ] Ensure env var names match viewer schema (`WEB_PORT`, not `CODE_SERVER_PORT`).
- [ ] Add a test that the generated YAML for web includes the expected command and port.

### Phase 3 — Add Deploy Modes to `azure_deploy_container.py`
- [ ] Add `--service` with choices: `web`, `web-caddy`, `ftp`, `full`.
- [ ] Implement `full` as an orchestrator deploying two container groups.
- [ ] Align naming:
  - base group uses `AZURE_CONTAINER_NAME`
  - ftp group uses `AZURE_CONTAINER_NAME-ftp`
  - dns labels: base and `-ftp`

### Phase 4 — Unify/Retire `csv_deploy_container.py`
Two options (choose one once Phase 3 is working):

- **Option A (preferred):** make `csv_deploy_container.py` a thin wrapper that calls `azure_deploy_container.py` for all modes, then deprecate/remove it later.
- **Option B:** keep `csv_deploy_container.py` as the viewer orchestrator and rewrite `azure_deploy_container.py` only for upstream compatibility.

Decision criteria:
- minimize future upstream sync conflicts
- reduce duplicate logic
- keep CI entrypoint stable

### Phase 5 — CI / Docs / Examples
- [ ] Update `.github/workflows/deploy.yml` to call the canonical deploy entrypoint.
- [ ] Update `docs/deploy/AZURE_CONTAINER.md` to reflect the new model and how compose drives deploy.
- [ ] Update `env.deploy.example` with any new required keys.

## Testing Plan
- [ ] Unit tests for compose detection:
  - errors when roles missing/ambiguous
  - correct selection when roles present
- [ ] YAML contract tests:
  - web container includes expected command
  - correct ports for web-caddy and ftp
  - `full` produces two YAMLs / two `az container create` calls (mocked)
- [ ] Keep existing suite green (`pytest tests/pytests/`).

## Rollout Plan
- Step 1: Land Phases 1–2 (web container boots reliably).
- Step 2: Land Phase 3 (modes + full orchestration).
- Step 3: Switch CI entrypoint and update docs.
- Step 4: Deprecate/remove the old entrypoint once stable.

## Risks / Watchouts
- Compose volumes: ACI cannot replicate arbitrary bind mounts; we must map only the supported “durable” mount(s) (Azure Files) and ignore dev-only mounts like `./.env:/app/.env`.
- Compose interpolation: ensure the PyYAML parser covers `${VAR:-default}` patterns used in the repo.
- Keeping upstream sync manageable: prefer moving reusable logic into helper modules rather than embedding huge logic inside the CLI file.
