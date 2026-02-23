# Shared Caddy Routing 

This guide explains how other projects deployed on the same Ubuntu server can register with the centralized Caddy proxy for automatic HTTPS routing — without needing their own sidecar proxies.

## How it works

The centralized proxy binds to host ports `80` and `443` and handles all SSL certificates.
Incoming requests are matched by domain name in `docker/proxy/Caddyfile` and routed internally via the external `caddy` Docker network to the appropriate container.

Registration is **fully automated** by the `ubuntu_deploy.py` script.  All you need to do is:

1. Prepare your project's `docker-compose.yml` (network + container name).
2. Set the right env vars in `.env.deploy`.
3. Run `ubuntu_deploy.py` — the Caddyfile is updated and Caddy reloaded for you.

## Step-by-step

### 1. Join the `caddy` network in your Compose file

Ensure your web-facing service connects to the external `caddy` network.  **Do not** publish host ports for your web service — Caddy handles external traffic.

```yaml
services:
  app:
    container_name: my-app-production   # explicit name avoids routing collisions
    # ports:                             # let Caddy handle external access
    #   - "8080:8080"
    networks:
      - internal
      - caddy

networks:
  internal:
    driver: bridge
  caddy:
    external: true
    name: caddy
```

> **Tip:** The `container_name` becomes the upstream target in the Caddyfile (`reverse_proxy my-app-production:8080`). Pick a name that is unique across all projects on the server.

### 2. Set env vars in `.env.deploy`

The deploy script derives all Caddy registration parameters from your env files:

| Variable | Purpose | Example |
|----------|---------|---------|
| `PUBLIC_DOMAIN` | The domain Caddy should route to this service | `myapp.example.com` |
| `WEB_PORT` | The port inside the container (default `3000`) | `8080` |
| `PORTAINER_STACK_NAME` | Used as the upstream service name | `my-app-production` |

```dotenv
# .env.deploy (relevant keys)
PUBLIC_DOMAIN=myapp.example.com
WEB_PORT=8080
PORTAINER_STACK_NAME=my-app-production
```

### 3. Deploy

```bash
source .venv/bin/activate
python scripts/deploy/ubuntu_deploy.py
```

The deploy script automatically:

1. Reads the proxy Caddyfile on the remote host via SSH.
2. Checks whether a site block for `PUBLIC_DOMAIN` already exists (idempotent).
3. If missing, appends a site block with `reverse_proxy <service>:<port>`.
4. Restarts the `central-proxy` container and validates the config.

No manual SSH or Caddyfile editing required.

## Manual fallback (reference)

The steps below are only needed if you are making ad-hoc changes outside the deploy pipeline, or need to debug a Caddy route.

### Edit the Caddyfile directly

On the server, edit the Caddyfile (typically at `/opt/camera-storage-viewer/docker/proxy/Caddyfile`):

```caddy
# -------------------------
# My App Route
# -------------------------
myapp.example.com {
    tls {$ACME_EMAIL}
    header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
    reverse_proxy my-app-production:8080
}
```

### Reload Caddy

The Caddyfile is bind-mounted into the container.  After editing the **host** file, restart the container (a simple `caddy reload` will not pick up the new inode):

```bash
docker restart central-proxy
docker exec central-proxy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```
