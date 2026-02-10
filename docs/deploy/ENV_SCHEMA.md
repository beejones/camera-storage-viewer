# Env schema: how to add a variable/secret

This repo intentionally uses a **strict, schema-driven** approach for all configuration keys used by:

- runtime `.env` (uploaded to Key Vault as a single secret)
- deploy-time `.env.deploy`
- GitHub Actions vars / secrets

The schema is the single source of truth:

- [scripts/deploy/env_schema.py](../scripts/deploy/env_schema.py)

If a key is not in the schema, it is treated as **unknown** and validation fails.

## Concepts

### Vars vs Secrets

- **Vars** are non-sensitive values (e.g. `AZURE_LOCATION`, `PUBLIC_DOMAIN`, `APP_IMAGE`, `CADDY_IMAGE`). They may be synced to GitHub Actions **variables**.
- **Secrets** are sensitive values (e.g. `GHCR_TOKEN`). They may be synced to GitHub Actions **secrets**.

In code:

- `VarsEnum` contains allowed variable keys.
- `SecretsEnum` contains allowed secret keys.

### Targets

Each key explicitly declares where it is expected to live via `EnvTarget`:

- `DOTENV_RUNTIME` → repo root `.env`
- `DOTENV_DEPLOY` → repo root `.env.deploy`
- `GH_ACTIONS_VAR` → GitHub Actions variable
- `GH_ACTIONS_SECRET` → GitHub Actions secret
- `KEYVAULT_SECRET` → Azure Key Vault secret name (rare; usually we upload full `.env` as one secret)

### Schema entries

Each key has an `EnvKeySpec`:

- `mandatory`: whether it must be present (after defaults are applied)
- `default`: optional default value
- `targets`: where the value is expected/allowed

There are two schemas:

- `RUNTIME_SCHEMA`: keys permitted in `.env`
- `DEPLOY_SCHEMA`: keys permitted in `.env.deploy`

## Add a new deploy-time variable (example)

Example: add a non-secret deploy config value `MY_FEATURE_FLAG` that should be stored in `.env.deploy` and also synced to GitHub Actions variables.

1) Add the enum entry

Edit [scripts/deploy/env_schema.py](../scripts/deploy/env_schema.py) and add:

- `VarsEnum.MY_FEATURE_FLAG = "MY_FEATURE_FLAG"`

2) Add it to `DEPLOY_SCHEMA`

Add a new `EnvKeySpec` entry:

- `key=VarsEnum.MY_FEATURE_FLAG`
- `mandatory=False` (or `True` if required)
- `default="false"` (optional)
- `targets={EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}`

3) Add it to the example env file

Update:

- [env.deploy.example](../env.deploy.example)

4) Use it in code via the enum

Never use raw string keys. Prefer:

- `os.getenv(VarsEnum.MY_FEATURE_FLAG.value)`

5) Run validation / tests

- `python scripts/deploy/validate_env.py`
- `pytest`

## Add a new secret (example)

Example: add `MY_API_TOKEN` as a deploy-time secret synced to GitHub Actions secrets.

1) Add enum entry:

- `SecretsEnum.MY_API_TOKEN = "MY_API_TOKEN"`

2) Add to `DEPLOY_SCHEMA` with:

- `targets={EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_SECRET}`

3) Update [env.deploy.example](../env.deploy.example) (leave empty value)

4) Use it in code via the enum:

- `os.getenv(SecretsEnum.MY_API_TOKEN.value)`

5) Re-run `validate_env.py` and `pytest`

## Runtime `.env` vs deploy `.env.deploy`

Rule of thumb:

- Put **app runtime config** in `.env` (things the container needs while running). This file is uploaded to Key Vault.
- Put **deployment config** in `.env.deploy` (Azure resource settings, image ref, GHCR credentials, etc.). This file is not uploaded to Key Vault.

## GitHub Actions syncing

- [scripts/deploy/gh_sync_actions_env.py](../scripts/deploy/gh_sync_actions_env.py) syncs schema keys to Actions vars/secrets.
- CI deployment uses Actions vars/secrets to generate `.env.deploy` on the runner.

If you add a key with `GH_ACTIONS_VAR` or `GH_ACTIONS_SECRET` targets, it will be included by the sync script automatically.

## Gotchas

- Unknown keys in `.env` or `.env.deploy` will fail validation.
- If a key is `mandatory=True`, it must be present (unless the deploy script derives it and injects it into the environment before validation).
- Don’t print secret values in logs. Vars can be logged.
