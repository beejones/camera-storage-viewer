# Review: protected-azure-container PR #11 — compose-driven ACI deployment

PR: https://github.com/beejones/protected-azure-container/pull/11
Title: `feat: Implement compose-driven ACI deployment by detecting services via x-deploy-role and adding related tests and documentation.`

## Summary
This PR moves `scripts/deploy/azure_deploy_container.py` toward a **docker-compose-as-source-of-truth** deployment model by:

- Detecting services using `x-deploy-role` via a new helper (`detect_services_by_role`).
- Passing Compose-derived **app ports**, **app command**, and **extra env** into YAML generation.
- Extending `DeployPlan` to carry additional fields (`app_ports`, `web_command`, `extra_env`) so hooks can mutate them.
- Adding CLI overrides for role detection ambiguity (e.g. explicitly selecting the app/caddy service).
- Adding documentation (`docs/deploy/COMPOSE_CONTRACT.md`) and tests for the new behavior.

Net: this is a meaningful step toward making deploy behavior match the compose contract instead of code-server-era assumptions.

## What Changed (high signal)
- `scripts/deploy/docker_compose_helpers.py`
  - Added `detect_services_by_role(compose_config) -> dict[str, list[str]]`.
  - Added `normalize_command(command) -> list[str]`.
- `scripts/deploy/azure_deploy_container.py`
  - Uses `detect_services_by_role()` to pick app/sidecar/ftp roles.
  - Adds CLI overrides for manual selection when auto-detection is ambiguous.
  - Extracts ports from compose and sets `config_app_ports`.
  - Extracts app `command` from compose and pushes it through to YAML.
  - Extracts `WEB_PORT` from compose `environment` and injects it into generated YAML via `extra_env`.
- `scripts/deploy/azure_deploy_yaml_helpers.py`
  - Adds support for:
    - multiple app ports (`app_ports`)
    - explicit app `command:` list
    - extra environment variables (`extra_env`)
  - Normalizes ports to a unique sorted list.
- `scripts/deploy/deploy_hooks.py`
  - Adds `extra_env: dict[str, str]` to `DeployPlan`.
- Tests
  - `tests/pytests/test_compose_detection.py`
  - `tests/pytests/test_aci_yaml_from_compose.py`
- Docs
  - `docs/deploy/COMPOSE_CONTRACT.md`

## Strengths
- **Correct direction:** service roles in compose (`x-deploy-role`) become the primary contract.
- **No Docker runtime requirement:** compose parsing remains PyYAML + interpolation.
- **Solves a real failure mode:** ACI app container needs the compose `command` (e.g., `uvicorn ...`) to actually run; image default entrypoint often isn’t enough.
- **Improved safety/operability:** string commands are executed consistently via `sh -lc` and ambiguous service detection can be resolved via CLI overrides.
- **Test coverage added:** basic detection + YAML command/port behavior is now asserted.

## Review Notes / Risks

### 1) Viewer compatibility: legacy `CODE_SERVER_PORT` injection
If `azure_deploy_yaml_helpers.generate_deploy_yaml` injects `CODE_SERVER_PORT` unconditionally, compose-driven web apps inherit a code-server-era env shape.

Update: PR refinements indicate `CODE_SERVER_PORT` is now only injected when `WEB_PORT` is missing (or otherwise not provided), preserving back-compat while keeping pure web deployments cleaner.

Remaining risk:
- Ensure tests cover both branches (`WEB_PORT` present vs missing) to prevent regressions.

### 2) Command normalization / execution semantics
Naively splitting string commands is fragile.

Update: PR refinements indicate all string commands are now wrapped as `['sh','-lc', <command>]`, which is the most reliable ACI execution strategy (and avoids tokenization/quoting edge cases).

Remaining risk:
- `sh -lc` changes execution semantics (shell expansion, quoting rules). That’s usually desired for compose-style strings, but should be documented as the contract.

### 3) Role detection ambiguity handling
Picking the “first” matching service is risky.

Update: PR refinements indicate the deploy script now fails fast (exit code 1) if multiple services are found for required roles (at least `app`) unless an explicit override is provided (e.g. `--compose-app-service`).

Remaining risk:
- Confirm the error message is actionable and lists candidate services.

### 4) “planning/…” file in upstream PR
The PR includes `planning/rewrite-azure_deploy_container-to-compose-engine.md` with camera-storage-viewer-specific context.

Suggestion:
- If `protected-azure-container` is meant to remain a generic template, consider moving this into the downstream repo, or rewriting it to be template-generic.

## Test Status
PR adds new tests:
- `tests/pytests/test_compose_detection.py`
- `tests/pytests/test_aci_yaml_from_compose.py`

Author reports focused test run coverage (`7 passed` across these new tests). PR page indicates checks passing (“2 / 2 checks OK”).

## Recommendation
**Approve** if the refinements described are present in the latest PR head.

Still worth addressing (small follow-ups):
- Add/adjust tests to cover the `CODE_SERVER_PORT` conditional behavior when `WEB_PORT` is present.
- Ensure ambiguity errors list candidate services and suggest the override flags.
- Decide whether the camera-storage-viewer-specific planning doc belongs in upstream.

## Suggested Follow-ups (non-blocking)
- Add a YAML contract test that ensures `WEB_PORT` is present when compose includes it, and that the app container actually binds `0.0.0.0` (document-level contract).
- Document that ACI does not honor compose `depends_on`, so sidecars must handle upstream readiness.
