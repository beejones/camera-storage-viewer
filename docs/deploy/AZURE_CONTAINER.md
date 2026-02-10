# Azure Container Deployment (ACI)

Deploy **camera-storage-viewer** to **Azure Container Instances (ACI)** as a lightweight **FTP endpoint** for cameras.

Current deployment model:

- Single container running an FTP server (`pyftpdlib`).
- Persistent storage via **Azure Files** mounted at `/data`.
- Runtime config stored as a single **Key Vault secret** named `env` (full `.env` content), fetched at startup via **Managed Identity**.

## Architecture

In Azure Container Instances (ACI), the most reliable model for this project is **two container groups**:

1) **FTP group** (public ports are consumed by FTP control + passive range)

| Container | Purpose | Ports |
|-----------|---------|-------|
| `camera-storage-viewer` | FTP server | 21 + passive range |

2) **Web group** (viewer app + optional Caddy sidecar)

| Container | Purpose | Ports |
|-----------|---------|-------|
| `camera-storage-viewer` | Viewer web API / UI | 8081 (internal), optionally 8081 public if deployed without Caddy |
| `caddy` (optional) | HTTPS termination / reverse proxy | 80, 443 |

Reason: ACI container groups can expose at most **5 public ports**. FTP typically consumes all 5 (21 + 4 passive), so HTTP/HTTPS must be a separate group.

## Prerequisites

- Azure CLI: `az login`
- Docker image pushed to GHCR/ACR
- `.env` file with FTP runtime config (runtime)
- `.env.deploy` file with Azure + deploy configuration (deploy-time)

Deployment reads `.env` first, then `.env.deploy` on top (deploy-time overrides).

## Step 1 — Create Azure Resources

The deploy script auto-creates resources if they don't exist:

```bash
python scripts/deploy/csv_deploy_container.py \
  --resource-group camera-storage-viewer-rg \
  --location westeurope
```

Creates:
- Resource group
- Managed Identity
- Storage account + file shares
- Key Vault (RBAC enabled)

## Step 2 — Upload runtime `.env` to Key Vault

```bash
python scripts/deploy/azure_upload_env.py \
  --vault camera-storage-viewer-rg-kv \
  --env-file .env
```

By default, deploy also uploads `.env` to Key Vault (controlled by `--upload-env` / `--no-upload-env`).

## Step 3 — Deploy to ACI

```bash
python scripts/deploy/csv_deploy_container.py \
  --resource-group camera-storage-viewer-rg \
  --image ghcr.io/<your-gh-username>/camera-storage-viewer:latest \
  --env-file .env.deploy
```

The script prints the ACI FQDN and the `ftp://` endpoint.

## Access

- **FTP**: `ftp://<dns-label>.<location>.azurecontainer.io:21`

Note: FTP passive mode requires opening the configured passive port range in ACI.

ACI limitation: container groups support at most **5 public ports total**, so keep the passive range small (e.g. `50000-50003`).

## Troubleshooting

### ACI `command` Overrides Docker `ENTRYPOINT`

Azure Container Instances treats a container `command:` as an **override** for the image's Docker `ENTRYPOINT`.
This repo relies on the image entrypoint (`/usr/local/bin/azure_start.sh`) to fetch Key Vault runtime env and then `exec` the application.

If your `docker-compose.yml` defines an app `command`, the deploy YAML generator will automatically prefix it with `/usr/local/bin/azure_start.sh` so the Key Vault fetch still runs.

If you're debugging a deployment where Key Vault logs are missing, check the app container's rendered YAML `command:` list includes `/usr/local/bin/azure_start.sh` as the first element.

### View Logs

```bash
az container logs --resource-group camera-storage-viewer-rg \
  --name camera-storage-viewer --container-name camera-storage-viewer
```

### Common Issues

| Issue | Solution |
|-------|----------|
| Camera can log in but uploads fail | Check passive ports are open and `FTP_PUBLIC_HOST` is set (if needed) |
| Connection times out | Confirm ACI has a public IP and port 21 is exposed |
| Files not persisted | Confirm Azure Files share is mounted at `/data` |

## GitHub Actions

See [.github/workflows/deploy.yml](../.github/workflows/deploy.yml) for automated deployment.
Triggers on: **Workflow Dispatch** (Manual)

### Required Setup

1. **Environment**: Create an environment named `production` in GitHub Settings.
2. **Secrets** (Environment or Repo):
   - `RUNTIME_ENV_DOTENV`: The **full content** of `.env` (excluding comments is fine).
3. **Variables** (Environment or Repo):
  - `AZURE_OIDC_APP_NAME` (Azure AD App Registration display name for OIDC)
   - `AZURE_CLIENT_ID` (OIDC App ID)
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`
   - `AZURE_RESOURCE_GROUP` (e.g. `camera-storage-viewer-rg`)
   - `AZURE_CONTAINER_NAME` (e.g. `camera-storage-viewer`)

The workflow builds/pushes `ghcr.io/<owner>/<repo>:latest` and then runs `scripts/deploy/csv_deploy_container.py`.

### Deploy Customizations (Hooks)

This repo supports a hooks-based customization system for viewer-specific deploy behavior.

- Default hooks module: `scripts/deploy/deploy_customizations.py`
- Override in CI by editing the deploy command to include:
  - `--hooks-module <module-or-path>`
  - `--hooks-soft-fail` (optional)

### Resetting GitHub Secrets

If you need to clear all secrets and variables (e.g. to fix stale environment overrides or reset completely):

**Option 1: Integrated Flag (Recommended)**
Runs before syncing new values:
```bash
python scripts/deploy/azure_deploy_container.py --nuke-github-secrets
```

**Option 2: Standalone Script**
```bash
python scripts/deploy/gh_nuke_secrets.py
```

*Note: You will be asked to type `DELETE` to confirm unless you run in non-interactive mode.*
