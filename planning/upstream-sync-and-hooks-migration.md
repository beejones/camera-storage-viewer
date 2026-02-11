# Plan: Sync upstream + migrate to deploy hooks (camera-storage-viewer)

Goal: regularly sync from `beejones/protected-azure-container` while keeping camera-storage-viewer-specific behavior in **downstream-only files**, using the new upstream **deploy hooks** system (PR #10) instead of maintaining a long-lived overlay fork of core deploy scripts.

Inputs:
- Upstream PR (hooks): https://github.com/beejones/protected-azure-container/pull/10
- Current downstream overlay deploy entrypoint: `scripts/deploy/csv_deploy_container.py`
- Current downstream YAML helpers: `scripts/deploy/csv_deploy_yaml_helpers.py`
- Current CI deploy entrypoint: `.github/workflows/deploy.yml` runs `python scripts/deploy/csv_deploy_container.py ...` (defaults to `--service full`).

## Phase 0 — Preconditions / decision points
1) **When to sync**
- Best: wait until upstream PR #10 is merged to `protected-azure-container/main`, then merge upstream `main` into this repo.
- If we need it immediately: cherry-pick the PR commit into the upstream remote branch we merge from.

2) **Scope reality check**
- Hooks enable *customization* (images/resources/YAML patching), but do not automatically add entirely new deploy “modes”.
- camera-storage-viewer has one major constraint that upstream does not: ACI public port limit → **two container groups** for `full` (FTP group + web/caddy group). If upstream deploy entrypoints still only deploy **one** container group at a time, we will keep a thin orchestrator script for “full” even after migrating to hooks.

Recommended migration posture:
- **Keep a minimal orchestrator** (for `full` split) if upstream doesn’t natively support multi-group deploy; migrate everything else into hooks.

## Phase 1 — Sync upstream
1) Fetch and merge upstream
- Ensure upstream remote exists (pointing at `beejones/protected-azure-container`).
- Merge upstream `main` into the current branch.
- Resolve conflicts with this bias:
  - Prefer upstream changes in upstream-owned files.
  - Keep viewer-specific changes either in:
    - hook module(s) (`scripts/deploy/deploy_customizations.py`), or
    - viewer-only scripts that orchestrate multiple deploy calls.

2) Validate after merge
- Run `pytest`.
- Smoke-import deploy entrypoints:
  - `python -c "import scripts.deploy.azure_deploy_container"`
  - `python -c "import scripts.deploy.deploy_hooks"` (if present after merge)

## Phase 2 — Introduce camera-storage-viewer hook module
Create a downstream-only hooks file:
- `scripts/deploy/deploy_customizations.py`

Implement only the hooks we need:
- `pre_validate_env(ctx)`
  - Inject safe defaults for viewer deploy (e.g., default `OUT_DIR=/data` if that’s the convention).
  - Keep this minimal to avoid “magic”.

- `post_validate_env(ctx)`
  - Enforce viewer-specific cross-field rules:
    - If deploying a web+caddy mode: require `PUBLIC_DOMAIN` and (optionally) `ACME_EMAIL`.
    - If deploying FTP on ACI: ensure passive range size is <= 4 ports so total public ports <= 5.

- `build_deploy_plan(ctx, plan)`
  - Apply image overrides:
    - Prefer GHCR mirror for Caddy when configured.
  - Apply viewer resource/port defaults:
    - Ensure web listens on the intended internal port (e.g. `8081`) when behind Caddy.
  - If upstream provides deploy metadata fields (e.g. `deploy_mode`, `deploy_role`): use them to switch behavior safely.

- `post_render_yaml(ctx, plan, yaml_text) -> str` (escape hatch)
  - Only if upstream YAML model cannot express a required viewer feature.
  - Examples:
    - Ensure `OUT_DIR=/data` and `WEB_PORT` env vars exist for the viewer container.

Hook configuration:
- Prefer auto-load via default `scripts/deploy/deploy_customizations.py`.
- Otherwise set `DEPLOY_HOOKS_MODULE=scripts.deploy.deploy_customizations` in `.env.deploy` / CI.

## Phase 3 — Replace the overlay deployment entrypoint
Target end-state:
- CI and docs call the upstream entrypoint, and downstream behavior comes from hooks.

### Option A (ideal): upstream deploy supports our modes
If upstream deploy script supports `--service` values compatible with camera-storage-viewer (`ftp`, `web`, `web-caddy`, `full`) and can deploy both container groups for `full`:
- Delete:
  - `scripts/deploy/csv_deploy_container.py`
  - `scripts/deploy/csv_deploy_yaml_helpers.py`
  - `scripts/deploy/csv_compose_helpers.py`
- Update:
  - `.github/workflows/deploy.yml` to call upstream `scripts/deploy/azure_deploy_container.py`.
  - `docs/deploy/AZURE_CONTAINER.md` and `env.deploy.example` to reference upstream entrypoint + `DEPLOY_HOOKS_MODULE`.

### Option B (pragmatic): keep a thin orchestrator for `full`
If upstream deploy still deploys only one container group:
- Keep `scripts/deploy/csv_deploy_container.py`, but refactor it to:
  - become a **thin orchestrator** that runs two deploys:
    - web/caddy container group
    - ftp container group
  - avoid copying upstream logic (no duplicated YAML generators if upstream can render YAML for each group)
  - push “per-group customization” into hooks.

In Option B, success criteria is: `csv_deploy_container.py` becomes small and stable, and conflicts during upstream sync are rare.

## Phase 4 — CI + docs migration
1) CI
- Update `.github/workflows/deploy.yml`:
  - Stop calling `scripts/deploy/csv_deploy_container.py` if Option A.
  - If Option B, keep calling it, but add `DEPLOY_HOOKS_MODULE` or ensure default auto-load works.

2) Deploy env example
- Update `env.deploy.example`:
  - Replace “Used by csv_deploy_container.py” note with the new entrypoint(s).
  - Add hook config keys:
    - `DEPLOY_HOOKS_MODULE` (optional if using default)
    - `DEPLOY_HOOKS_SOFT_FAIL` (optional)

3) Docs
- Update `docs/deploy/AZURE_CONTAINER.md`:
  - Replace command examples to match the chosen option.
  - Add a short section “Customizations via hooks” pointing at `scripts/deploy/deploy_customizations.py`.

## Phase 5 — Tests
1) Hook unit tests (downstream)
- Add a small test module ensuring our hook behaviors:
  - port-range validation
  - caddy image override
  - post_render_yaml patching (if used)

2) Integration expectations
- Ensure `pytest` remains green.
- Keep tests stable by asserting on deterministic markers (e.g., appended YAML comments) rather than formatting-sensitive YAML.

## Deliverables checklist
- [ ] Upstream merged (includes deploy hooks)
- [ ] `scripts/deploy/deploy_customizations.py` added with viewer hook logic
- [ ] CI + docs updated to use hooks-based flow
- [ ] Overlay removed (Option A) or minimized (Option B)
- [ ] Tests updated/added; `pytest` green
