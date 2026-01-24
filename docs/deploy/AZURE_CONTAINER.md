# Azure Container Deployment (ACI)

Deploy **camera-storage-viewer** to **Azure Container Instances (ACI)** with:

- **code-server** (VS Code in browser) as the main interface
- **Caddy** sidecar for **TLS** with automatic Let's Encrypt certificates
- **Basic Auth** protection
- **Azure Key Vault** + **Managed Identity** for secrets
- **Azure Files** persistence for workspace and config

## Architecture

ACI container group with 2 containers:

| Container | Purpose | Ports |
|-----------|---------|-------|
| `camera-storage-viewer` | code-server | 8080 (internal) |
| `tls-proxy` (Caddy) | TLS + Basic Auth | 80, 443 (public) |

```
Internet → Caddy (:443) → [TLS + Basic Auth] → code-server (:8080)
```

## Prerequisites

- Azure CLI: `az login`
- Docker image pushed to GHCR/ACR
- `.env` file with Basic Auth credentials (runtime)
- `.env.deploy` file with Azure + deploy configuration (deploy-time)

Deployment reads `.env` first, then `.env.deploy` on top (deploy-time overrides).

## Step 1 — Create Azure Resources

The deploy script auto-creates resources if they don't exist:

```bash
python scripts/deploy/azure_deploy_container.py \
  --resource-group camera-storage-viewer-rg \
  --location westeurope
```

Creates:
- Resource group
- Managed Identity
- Storage account + file shares
- Key Vault (RBAC enabled)

## Step 2 — Configure Authentication

### Generate Basic Auth Hash

```bash
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'your-password'
```

Add to `.env`:

```bash
BASIC_AUTH_USER=admin
BASIC_AUTH_HASH=$2a$14$...your-hash...
```

### Upload to Key Vault

```bash
python scripts/deploy/azure_upload_env.py \
  --vault camera-storage-viewer-rg-kv \
  --env-file .env
```

## Step 3 — Deploy to ACI

```bash
python scripts/deploy/azure_deploy_container.py \
  --resource-group camera-storage-viewer-rg \
  --image ghcr.io/<your-gh-username>/camera-storage-viewer:latest \
  --public-domain your-domain.com \
  --acme-email you@your-domain.com \
  --env-file .env.deploy
```

## Step 4 — DNS Setup

Point your domain CNAME to the ACI FQDN:

```bash
az container show \
  --name camera-storage-viewer \
  --resource-group camera-storage-viewer-rg \
  --query ipAddress.fqdn -o tsv
```

Result: `<dns-label>.<location>.azurecontainer.io`

## Access

- **VS Code**: `https://your-domain.com/`
- **Health check**: `https://your-domain.com/healthz`

## Troubleshooting

### View Logs

```bash
# code-server container
az container logs --resource-group camera-storage-viewer-rg \
  --name camera-storage-viewer --container-name camera-storage-viewer

# Caddy container
az container logs --resource-group camera-storage-viewer-rg \
  --name camera-storage-viewer --container-name tls-proxy
```

### Common Issues

| Issue | Solution |
|-------|----------|
| 502 Bad Gateway | Check code-server is running in app container |
| TLS cert error | Verify domain DNS points to ACI IP |
| Auth not working | Verify BASIC_AUTH_HASH is valid bcrypt |

## GitHub Actions

See [.github/workflows/deploy.yml](../.github/workflows/deploy.yml) for automated deployment.
Triggers on: **Workflow Dispatch** (Manual)

### Required Setup

1. **Environment**: Create an environment named `production` in GitHub Settings.
2. **Secrets** (Environment or Repo):
   - `RUNTIME_ENV_DOTENV`: The **full content** of `.env` (excluding comments is fine).
   - `BASIC_AUTH_HASH`: The bcrypt hash for Basic Auth.
3. **Variables** (Environment or Repo):
   - `AZURE_CLIENT_ID` (OIDC App ID)
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`
   - `AZURE_RESOURCE_GROUP` (e.g. `camera-storage-viewer-rg`)
   - `AZURE_CONTAINER_NAME` (e.g. `camera-storage-viewer`)
   - `AZURE_PUBLIC_DOMAIN` (e.g. `your-domain.com`)
   - `AZURE_ACME_EMAIL`
   - `BASIC_AUTH_USER`
