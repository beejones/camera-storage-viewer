# Ubuntu Server Deployment

This document describes how to deploy `camera-storage-viewer` alongside a central Caddy proxy and Portainer administrator panel on a standalone Ubuntu server.

## Architecture

This setup uses a **Centralized Proxy** approach:
- A single **Caddy** instance binds to host ports `80` and `443`.
- All other containers (like `portainer` and `camera-storage-viewer`) bind no host ports.
- Caddy acts as the TLS terminator and routes incoming traffic to the appropriate containers via an external Docker network named `caddy`.

## Prerequisites

- Ubuntu 24.04 LTS (recommended)
- Docker Engine + Docker Compose plugin (`docker compose`)
- SSH access to the server (key-based auth recommended)
- `rsync` installed locally and on the server
- **Firewall**: Ensure your server's firewall allows inbound TCP traffic on ports `80` and `443` ONLY. Do not expose `9000` or `9443` directly.

## Initial Server Setup

Follow these steps once on a fresh Ubuntu server.

### 0. Configure DNS

Before deploying Caddy, you must configure your DNS provider so Let's Encrypt can issue certificates:

Create two `A` records (or `CNAME` records) pointing to your Ubuntu server's public IP address:
- `portainer.your-domain.com` (for the admin panel)
- `camera-storage-viewer.your-domain.com` (for the actual app)

*If your DNS is not propagated, Caddy will fail to get SSL certificates and will return 502 Bad Gateway errors.*

### 1. Stand up Centralized Caddy

The proxy configuration lives in `docker/proxy/docker-compose.yml` and `docker/proxy/Caddyfile`.
Instead of manually copying and starting it, you can use the built-in deployment script which reads the host and domains from your `.env` files.

Ensure your `.env` or `.env.secrets` has:
```env
ACME_EMAIL=your-email@example.com
```
And your `.env.deploy` has at minimum:
```env
UBUNTU_SSH_HOST=ronny@<server-ip>
PUBLIC_DOMAIN=example.com
```

Then run the bash script:

```bash
# On your local machine, from the repository root:
bash scripts/deploy/ubuntu_deploy_proxy.sh
```

*(This script automatically deploys to `~/containers/central-proxy` and ensures the external `caddy` docker network is created on the remote host.)*

### 2. Stand up Portainer (Admin UI)

Deploy Portainer and attach it to the `caddy` network. **Notice we do not publish any ports (`-p`)**.

```bash
# On the server:
docker volume create portainer_data

docker run -d \
  --name portainer \
  --restart=unless-stopped \
  --network caddy \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v portainer_data:/data \
  portainer/portainer-ce:latest
```

Wait a moment, then securely access Portainer via Caddy at `https://portainer.example.com`.
Set up your initial admin password.

## Deploying Camera Storage Viewer

Now that the infrastructure is ready, deploy `camera-storage-viewer`.

### Automatic Deployment (Recommended)

Use the built-in deploy script that uses SSH to copy files and triggers a Portainer webhook. 

Ensure you have created a Stack in Portainer named `camera-storage-viewer` and enabled its Webhook.

**Configure `.env.deploy.secrets` locally:**
```env
PORTAINER_WEBHOOK_TOKEN=<token-tail-only>
PORTAINER_ACCESS_TOKEN=<your-portainer-api-token>
```

**Deploy:**
```bash
source .venv/bin/activate
python scripts/deploy/ubuntu_deploy.py \
  --remote-dir /opt/camera-storage-viewer \
  --sync-secrets
```

This pushes the image, syncs `docker-compose.yml` and `.env` files, and triggers Portainer to pull and restart the stack.

## Troubleshooting Caddy

If certificates fail to provision or routes return 502, inspect Caddy's logs:

```bash
docker logs central-proxy
```

Common issues:
- **Rate Limits**: Let's Encrypt limits failed validations. Double-check your DNS points to the correct IP.
- **Container Not Found**: Caddy resolves upstream targets by their container name (e.g., `reverse_proxy portainer:9000`). If the container is not on the `caddy` network or its name differs, Caddy will return a 502 error.

## Adding Other Projects to Caddy

If you have additional services on the same server that need HTTPS routing through the centralized proxy, see [Shared Caddy Routing](SHARED_CADDY_ROUTING.md).

