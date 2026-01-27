# Plan: Deployment Customization Hooks

Goal: Provide **stable customization hooks** in the deployment scripts so camera-storage-viewer (or any downstream) can add/override behavior in **separate files** while keeping upstream deploy scripts mostly unchanged and easy to re-sync.

This plan assumes the deploy entrypoints live under `scripts/deploy/` and generate ACI YAML plus run `az` commands.

---

## Design Goals

- **No merge pain on re-sync**: downstream custom logic lives in separate files/modules.
- **Opt-in**: if no customization module exists, deploy behaves exactly like upstream.
- **Explicit extension points**: small, named hook calls rather than “monkey patching”.
- **Safe execution**: clear error messages and predictable failure behavior.
- **Testable**: hooks can be unit-tested without Azure.
- **Backwards compatible hook API**: once published, avoid breaking the hook signatures.

Non-goals:
- A full plugin system with discovery across packages.
- Runtime (in-container) customization; this is deploy-time only.

---

## Upstream-first Workflow (How we will build this)

We implement the hook mechanism **in upstream first** (protected-azure-container), then adapt camera-storage-viewer to use it.

1) **Upstream implementation**
- Add the minimal loader + hook runner (`scripts/deploy/deploy_hooks.py`).
- Add small hook call sites in upstream deploy scripts.
- Add tests for hook loading and ordering.

2) **Downstream integration**
- Create `scripts/deploy/deploy_customizations.py` in camera-storage-viewer.
- Move camera-storage-viewer-specific behavior into hooks.
- Keep upstream-owned files as close to upstream as possible.

---

## Hook Discovery & Configuration

### A) Single optional module (simplest)
- Default module path: `scripts/deploy/deploy_customizations.py`
- If it exists and is importable, enable hooks.
- The deployment script must **check whether each hook exists** and execute it only if present (missing hooks are treated as no-op).
- If it does not exist: silently no-op.

### B) Configurable module path (recommended)
Allow selecting a different hook module without renaming files:

- Env: `DEPLOY_HOOKS_MODULE=scripts.deploy.my_hooks`
- CLI: `--hooks-module scripts.deploy.my_hooks`

Resolution order:
1. CLI `--hooks-module`
2. `DEPLOY_HOOKS_MODULE`
3. Default `scripts.deploy.deploy_customizations` (optional)



Behavior:
- If module name is explicitly provided (CLI/env) and import fails ⇒ **hard error**.
- If only the default is attempted and it’s missing ⇒ **no-op**.

---

## Hook API Shape

### Recommended: “Hook object” with named methods
Provide a single class (or object) that implementers can partially implement.

Example contract:
- Module exports `get_hooks() -> DeployHooks`
- `DeployHooks` is a Protocol/ABC defining methods; missing methods treated as no-op.

This is preferred over “many standalone functions” because:
- versioning is easier (add new optional methods)
- state can be shared if needed

### Compatibility: also allow standalone functions

To keep this very easy to adopt, the loader may also support a module that exports standalone hook functions.

Implementation detail:
- The loader can create a lightweight adapter object that exposes methods if the module provides matching functions.
- Either way, deploy scripts just call `call_hook(hooks, "hook_name", ...)`.

### Hook Context Object
Pass a single context object to all hooks to avoid changing signatures frequently.

Suggested dataclasses:

- `DeployContext`
  - `repo_root: Path`
  - `env: dict[str, str]` (merged runtime + deploy env)
  - `args: argparse.Namespace`
  - `compose_defaults: ComposeDeployDefaults | None`
  - `logger` (or simple `print` wrapper)

- `DeployPlan` (mutable)
  - `service: Literal['ftp','web','web-caddy','full', ...]`
  - `container_group_name: str`
  - `dns_label: str`
  - `image_refs: {app,caddy,other,...}`
  - `resources: {app_cpu/app_mem,...}`
  - `ports: {...}`
  - `shares_to_mount: {...}`
  - `web_command: list[str] | None`
  - `extra_metadata: dict[str, Any]` (future-proof bag)

- `RenderedYaml`
  - `text: str`
  - (optional future) `model: dict[str, Any]` if we move to structured YAML generation

Hooks can either:
- mutate `DeployPlan` before YAML is rendered
- post-process the rendered YAML string

---

## Hook Points (Execution Order)

Define **small, stable extension points** where custom code can run.

### 1) `pre_validate_env(ctx)`
- Runs before `.env/.env.deploy` strict validation.
- Use cases:
  - inject/override defaults for downstream
  - allow additional env keys (if you maintain a separate schema overlay)

### 2) `post_validate_env(ctx)`
- Runs after validation and defaults are applied.
- Use cases:
  - enforce extra cross-field rules

### 3) `build_deploy_plan(ctx, plan) -> plan`
- Runs after arguments + compose defaults are loaded, before YAML generation.
- Use cases:
  - override image refs (e.g. GHCR mirrors)
  - tweak resource sizing for specific environments
  - change DNS label strategy
  - modify ports/passive ranges for FTP

### 4) `pre_render_yaml(ctx, plan)`
- Last chance to adjust the plan before generating YAML.

### 5) `post_render_yaml(ctx, plan, rendered) -> rendered`
- Post-process YAML.
- Use cases:
  - add extra volume mounts or env vars in ways upstream doesn’t model yet
  - apply one-off patches until upstream supports a feature cleanly

### 6) `pre_az_apply(ctx, plan, yaml_path)`
- Right before `az container create ...`.
- Use cases:
  - print extra diagnostics
  - run pre-flight checks

### 7) `post_deploy(ctx, plan, deploy_result)`
- After deploy succeeds.
- Use cases:
  - emit DNS instructions
  - run health checks

### 8) `on_error(ctx, exc)`
- Optional error hook.
- Must never swallow exceptions unless explicitly configured.

---

## Failure Semantics

Make failure behavior explicit and consistent.

Recommended:
- Default: hook exceptions **fail the deploy** (because it’s your custom logic).
- Add a mode to tolerate hook errors:
  - CLI: `--hooks-soft-fail`
  - Env: `DEPLOY_HOOKS_SOFT_FAIL=true`
  - When enabled: log warning and continue.

---

## Minimal Code Changes Required (Upstream-Friendly)

To minimize merge conflicts on future upstream syncs:

- Create a small helper module in upstream-owned area, e.g. `scripts/deploy/deploy_hooks.py`, containing:
  - `load_hooks(module_name: str | None) -> Hooks`
  - `call_hook(hooks, name, *args, **kwargs)`
  - small, stable dataclasses/protocols for `DeployContext`, `DeployPlan`, etc.

- In the main deploy script(s), add only a few lines at each extension point:
  - `hooks = load_hooks(...)`
  - `call_hook(hooks, 'pre_validate_env', ctx)`
  - etc.

This keeps diffs small and reduces conflict surface.

---

## How Downstream Customizations Should Look

Example file: `scripts/deploy/deploy_customizations.py`

- Provides `get_hooks()` returning an object that implements any subset of hook methods.
- Contains only downstream-specific behavior.

Example use cases for camera-storage-viewer:
- Force `service=full` on CI deploy.
- Enforce ACI port constraints by splitting groups (web+caddy vs ftp).
- Apply `CADDY_IMAGE` override (GHCR mirror) when Docker Hub is flaky.
- Add viewer-specific env upload prefixes (`FTP_`, `OUT_DIR`, `RETENTION_`, `WEB_PORT`, `THUMBNAIL_`).

---

## Camera-storage-viewer: Required Customization Hook List

This list is the **minimum set of hook behaviors** camera-storage-viewer needs to remain stable and Azure-friendly. These can be implemented either as methods on a hooks object or as standalone functions.

1) `build_deploy_plan(ctx, plan)`
- Ensure **two container groups** for `service=full`:
  - `web + caddy` group exposes 80/443 (and/or 8081 if no caddy)
  - `ftp` group exposes 21 + passive range
- Reason: ACI max 5 public ports per container group.

2) `post_validate_env(ctx)`
- Enforce viewer-specific cross-field rules, e.g.:
  - if deploying with web+caddy/full, require `PUBLIC_DOMAIN` (or explicitly allow a “no-domain HTTP-only” mode)
  - validate FTP passive port range <= 4 ports (so total <= 5)

3) `build_deploy_plan(ctx, plan)` (image overrides)
- Apply Caddy image mirroring rules (prefer GHCR mirror) when configured.
- Keep upstream defaults unless override is set.

4) `pre_validate_env(ctx)` (optional)
- If camera-storage-viewer wants extra deploy/runtime keys beyond upstream schema, inject defaults here (or document that schema overlay is needed).

5) `post_render_yaml(ctx, plan, rendered)` (optional escape hatch)
- Apply temporary YAML patches if upstream YAML model can’t represent a required ACI feature yet.
- Goal is to delete this hook usage once upstream supports it cleanly.

---

## Testing Strategy

- Unit tests for `deploy_hooks.load_hooks()`:
  - default module missing ⇒ no-op
  - explicit module missing ⇒ error
  - soft-fail behavior

- Contract tests for hook execution order:
  - use a fake hooks module that records calls

- YAML patch tests for `post_render_yaml`:
  - ensure patches are applied deterministically

---

## Rollout Steps

1. Add `scripts/deploy/deploy_hooks.py` with hook loader + call helper + context/plan dataclasses.
2. Add hook calls to deploy entrypoint(s) at the points listed above.
3. Add documentation section “Customizing deployments” referencing `DEPLOY_HOOKS_MODULE` and the default `deploy_customizations.py`.
4. Add a sample customization file template (optional) under `docs/` (not enabled by default).
5. Add tests for loader + execution ordering.

---

## Open Questions (Decide Up Front)

- Should hooks be allowed to modify the env schema (add keys) or only consume existing schema keys?
- Do we want to support multiple hook modules (chain) or exactly one?
- Should `post_render_yaml` be string-based only, or do we invest in a structured YAML model first?

Recommendation for now:
- **Single module**, **string post-process allowed**, keep it simple, iterate later.
