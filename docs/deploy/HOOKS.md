# Deployment Customization Hooks

This repository allows downstream consumers (forks or layered repositories) to customize the deployment process without modifying the core `scripts/deploy/azure_deploy_container.py` script. This makes it easier to pull upstream updates while maintaining custom behavior.

## Overview

Hooks are Python callback methods that run at specific points in the deployment lifecycle. You can implement these hooks to:

- Override default configuration (e.g., container images, resources).
- Enforce custom validation rules.
- Modify the generated Azure Container Instances (ACI) YAML.
- Run pre-flight or post-deploy tasks.

## Configuration

### Loader Precedence
The hook loader follows this order of precedence for configuration:
1. **CLI Argument**: `--hooks-module <path>`
2. **Environment Variable**: `DEPLOY_HOOKS_MODULE=<path>`
3. **Default**: `scripts/deploy/deploy_customizations.py` (relative to repo root)

> [!IMPORTANT]
> - If an explicit module or path is provided and it cannot be loaded, the deployment will **hard fail**.
> - If the default file `scripts/deploy/deploy_customizations.py` exists but fails to import (e.g., due to syntax errors), the deployment will **hard fail**.
> - Hooks are only skipped (no-op) if the default file is missing and no module was specified.

### Soft Fail Precedence
By default, hook failures abort the deployment. You can enable "soft-fail" mode (log error and continue):
1. **CLI Argument**: `--hooks-soft-fail`
2. **Environment Variable**: `DEPLOY_HOOKS_SOFT_FAIL=true`
3. **Default**: `false`

> [!NOTE]
> When `soft-fail` is enabled, it also covers **loading failures**. If a hook module exists but fails to import, the loader will log a warning and return a dummy hooks object (no-op) instead of raising an error.

## Implementing Hooks

Create a Python module that exports a `get_hooks()` function returning an object (class instance or simple object) that implements any of the methods defined in the protocol. You only need to implement the hooks you care about.

### Example `deploy_customizations.py`

```python
import os
from scripts.deploy.deploy_hooks import DeployContext, DeployPlan

class MyHooks:
    def pre_validate_env(self, ctx: DeployContext) -> None:
        """Called before .env files are validated. Good for injecting defaults."""
        # ctx.env is a live view of os.environ.
        # Modifying it here affects subsequent validation and deployment.
        if "MY_CUSTOM_VAR" not in ctx.env:
             print("Injecting default for MY_CUSTOM_VAR")
             ctx.env["MY_CUSTOM_VAR"] = "default-value"

    def build_deploy_plan(self, ctx: DeployContext, plan: DeployPlan) -> None:
        """Called before YAML generation. Modify the plan here."""
        # Use plan metadata to decide behavior
        if plan.deploy_mode == "full" and plan.deploy_role == "app":
             plan.app_cpu = 4.0
             plan.web_command = ["./start-custom.sh"]

    def post_render_yaml(self, ctx: DeployContext, plan: DeployPlan, yaml_text: str) -> str:
        """Called after YAML generation. Return modified YAML."""
        return yaml_text + "\n# Patched by custom hook"

def get_hooks():
    return MyHooks()
```

## Hook Reference

### `pre_validate_env(ctx)`
- **Summary**: Called before strict schema validation and before `.env` files are loaded.
- **Environment Mutability**: `ctx.env` is a mutable mapping. Changes made here are synchronized back to `os.environ` and are visible to the subsequent validation steps.

### `post_validate_env(ctx)`
- **Summary**: After environment variables are loaded and validated.
- **Use for**: Enforcing cross-field validation rules specific to your deployment.

### `build_deploy_plan(ctx, plan)`
- **Summary**: After parsing arguments and Docker Compose defaults, but before generating YAML.
- **Use for**: Overriding images, resources, ports, or command. This is the **preferred** place for most customizations.

### `pre_render_yaml(ctx, plan)`
- **Summary**: Just before the YAML generation function is called.

### `post_render_yaml(ctx, plan, yaml_text) -> str`
- **Summary**: After YAML generation.
- **Use for**: String-based patching of result YAML.

### `pre_az_apply(ctx, plan, yaml_path)`
- **Summary**: Before `az container create` is executed.

### `post_deploy(ctx, plan, deploy_result)`
- **Summary**: After successful deployment.

### `on_error(ctx, exc)`
- **Summary**: If an exception occurs during the deployment lifecycle.
- **Note**: This catch includes deployment runtime exceptions but excludes `SystemExit` (e.g. from argparse or direct validation skips).

## Data Structures

### `DeployContext`
- `repo_root`: Path to repository root.
- `env`: Mutable mapping of current environment variables.
- `args`: Parsed command-line arguments.

### `DeployPlan`
Mutable object representing the deployment configuration:
- `deploy_mode`: (e.g., "full", "web-caddy") - the requested deployment mode.
- `compose_service_name`: (e.g., "web") - the specific service being processed.
- `deploy_role`: (e.g., "app", "caddy", "other") - the role in the deployment.
- `app_port`: (int) The principal application port.
- `app_ports`: (list[int]) All ports exposed by the application container.
- `service_mode`: (str) Deployment mode (e.g., "app", "sidecar", "worker").
- `ftp_passive_range`: (str) FTP passive port range if applicable (e.g., "30000-30009").
- `web_command`: list of strings for the application entrypoint.
- `extra_metadata`: A dictionary for custom data.
