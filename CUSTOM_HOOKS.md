# Custom Deploy Hooks

This repo supports **deployment customization hooks** to keep viewer-specific behavior out of the upstream-ish deploy scripts.

Hooks are loaded by [scripts/deploy/deploy_hooks.py](scripts/deploy/deploy_hooks.py) and are called from deploy entrypoints such as:
- [scripts/deploy/csv_deploy_container.py](scripts/deploy/csv_deploy_container.py)
- [scripts/deploy/azure_deploy_container.py](scripts/deploy/azure_deploy_container.py)

## What you get

- A stable set of hook points (pre/post validation, plan mutation, YAML mutation, etc.)
- A `DeployContext` that includes a **mutable** `env` mapping (usually backed by `os.environ`)
- A `DeployPlan` object you can modify in `build_deploy_plan`

This is intended for:
- setting defaults before strict validation
- enforcing project-specific constraints (e.g. ACI port limits)
- overriding images/ports/metadata without forking core deploy logic

## How hooks are discovered

Resolution order in `deploy_hooks.load_hooks(...)`:

1) CLI flags (hard requirement if provided)
- `--hooks-module <module-or-path>`
- `--hooks-soft-fail`

2) Environment variables
- `DEPLOY_HOOKS_MODULE` (module path like `mypkg.myhooks` or a file path like `/abs/path/hooks.py`)
- `DEPLOY_HOOKS_SOFT_FAIL=true` (string `true` enables soft-fail)

3) Default file (auto-load if present)
- `scripts/deploy/deploy_customizations.py`

If nothing is configured and the default file does not exist, hooks are a no-op.

## Quick start: create your own hooks module

### Option A (recommended): edit the default module

Use the existing module:
- [scripts/deploy/deploy_customizations.py](scripts/deploy/deploy_customizations.py)

It’s already auto-loaded if present.

### Option B: create a separate file and point at it

Example:

```bash
python scripts/deploy/csv_deploy_container.py \
  --env-file .env.deploy \
  --hooks-module ./my_hooks.py
```

Or via env var:

```bash
export DEPLOY_HOOKS_MODULE=./my_hooks.py
python scripts/deploy/csv_deploy_container.py --env-file .env.deploy
```

## Hook interface

Hooks are **optional**: implement only the methods you need.

The protocol is defined in [scripts/deploy/deploy_hooks.py](scripts/deploy/deploy_hooks.py):

- `pre_validate_env(ctx)`
  - Runs before schema validation
  - Best for injecting safe defaults: `ctx.env.setdefault("KEY", "value")`

- `post_validate_env(ctx)`
  - Runs after schema validation
  - Best for cross-field checks and guardrails that should fail fast

- `build_deploy_plan(ctx, plan)`
  - Mutate `plan` fields (image overrides, ports, metadata)

- `pre_render_yaml(ctx, plan)`
  - Called before generating YAML (if wired in the calling script)

- `post_render_yaml(ctx, plan, yaml_text) -> str`
  - Return modified YAML text (if wired in the calling script)

- `pre_az_apply(ctx, plan, yaml_path)`
  - Called right before `az container create` / apply step (if wired)

- `post_deploy(ctx, plan, deploy_result)`
  - Called after deploy (if wired)

- `on_error(ctx, exc)`
  - Called when the deploy script catches an exception (if wired)

Note: Not every entrypoint wires every hook yet. The hook system is safe to call even when hooks are missing.

## Minimal example (object + get_hooks)

Create `my_hooks.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from scripts.deploy.deploy_hooks import DeployContext, DeployPlan


@dataclass
class MyHooks:
    def pre_validate_env(self, ctx: DeployContext) -> None:
        # Only set defaults; avoid overwriting user-provided values.
        ctx.env.setdefault("WEB_PORT", "8081")

    def post_validate_env(self, ctx: DeployContext) -> None:
        # Example: enforce an invariant.
        if ctx.env.get("GHCR_PRIVATE", "false").lower() == "true" and not ctx.env.get("GHCR_TOKEN"):
            raise ValueError("GHCR_TOKEN is required when GHCR_PRIVATE=true")

    def build_deploy_plan(self, ctx: DeployContext, plan: DeployPlan) -> None:
        # Example: override the caddy image.
        plan.caddy_image = ctx.env.get("CADDY_IMAGE", plan.caddy_image)


def get_hooks() -> MyHooks:
    return MyHooks()
```

## Alternative: module-level functions (no get_hooks)

If your module does not define `get_hooks()`, the loader treats the module itself as the hook object.

```python
# my_hooks.py
from scripts.deploy.deploy_hooks import DeployContext

def pre_validate_env(ctx: DeployContext) -> None:
    ctx.env.setdefault("OUT_DIR", "/data")
```

## Soft-fail behavior

If a hook raises an exception:

- Default: deployment aborts (hard fail)
- With soft-fail enabled:
  - `--hooks-soft-fail` or `DEPLOY_HOOKS_SOFT_FAIL=true`
  - The error is printed and deploy continues

Use soft-fail sparingly: it’s mainly useful when experimenting or when hooks are “nice-to-have”.
