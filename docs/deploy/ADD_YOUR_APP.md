# Add your own app to the container

This repo supports deploying an app to **Azure Container Instances** (ACI) using **docker-compose.yml as the source of truth**.

The deploy engine reads Compose to determine:
- which service is the **app** vs **sidecar** (via `x-deploy-role`)
- the app **command** and **ports**
- selected environment values such as `WEB_PORT`

See the Compose expectations here: [docs/deploy/COMPOSE_CONTRACT.md](COMPOSE_CONTRACT.md).

## Recommended approach
1. Define your services in `docker-compose.yml`.
	- Mark the “main app” with `x-deploy-role: app`.
	- Mark a reverse-proxy (if used) with `x-deploy-role: sidecar`.
2. Put the runtime contract in Compose (command/ports/env).
	- If your app listens on a non-default port, set `WEB_PORT` in the app service environment.
3. Keep repo-specific Azure behavior in **deploy hooks** instead of forking the deploy script.

## Customizing deployment via hooks
This repo uses the upstream deploy hooks system to keep template code generic while allowing app-specific behavior.

- Hook entrypoint example: [scripts/deploy/deploy_customizations.py](../../scripts/deploy/deploy_customizations.py)
- Hook interface: [scripts/deploy/deploy_hooks.py](../../scripts/deploy/deploy_hooks.py)
- Hook usage doc: [CUSTOM_HOOKS.md](../../CUSTOM_HOOKS.md)

Common hook use-cases:
- Setting safe defaults in `pre_validate_env` (e.g. default `WEB_PORT`, `OUT_DIR`).
- Enforcing constraints in `post_validate_env` (e.g. ACI public ports limits).
- Mutating the `DeployPlan` in `build_deploy_plan` (e.g. images, sidecars, ports).

For deployment instructions, see [docs/deploy/AZURE_CONTAINER.md](AZURE_CONTAINER.md).
