# Ubuntu / Portainer Deployment Integration Plan

## Overview

Merge the upstream `_upstream` changes into this repo to add full Ubuntu / Portainer-managed
deployment support alongside the existing Azure Container Instance (ACI) deploy path.
After this work the command `python scripts/deploy/ubuntu_deploy.py` (no args) must succeed
by reading all required values from the `.env*` files.

---

## Task Checklist

### Phase 1 — Cleanup & Audit (prerequisite)
- [ ] P1.1 Review all files that differ between `_upstream` and current `scripts/deploy/`
- [ ] P1.2 Update `AGENT.md` — replace stale "Stock Dashboard" project context with camera-storage-viewer specifics
- [ ] P1.3 Identify env-schema merge conflicts (upstream adds `DOTENV_SECRETS`, `DOTENV_DEPLOY_SECRETS` targets and `SECRETS_SCHEMA`; local adds camera FTP vars)

**Phase exit criteria:** all upstream-vs-local diffs are categorised as "copy as-is", "merge", or "skip (local-only addon)".

---

### Phase 2 — Merge Changed Upstream Deploy Scripts
Files that differ and need careful merge (preserve local camera FTP additions).
> "upstream" throughout this plan means `scripts/deploy/_upstream/scripts/deploy/`.

- [ ] P2.1 `scripts/deploy/env_schema.py` — merge upstream `DOTENV_SECRETS` / `DOTENV_DEPLOY_SECRETS` targets + `SECRETS_SCHEMA` block into local (which adds camera FTP vars)
- [ ] P2.2 `scripts/deploy/validate_env.py` — merge upstream changes
- [ ] P2.3 `scripts/deploy/docker_compose_helpers.py` — merge upstream changes
- [ ] P2.4 `scripts/deploy/azure_deploy_container.py` — merge upstream changes
- [ ] P2.5 `scripts/deploy/azure_deploy_yaml_helpers.py` — merge upstream changes
- [ ] P2.6 `scripts/deploy/azure_upload_env.py` — merge upstream changes
- [ ] P2.7 `scripts/deploy/gh_nuke_secrets.py` — merge upstream changes
- [ ] P2.8 `scripts/deploy/gh_sync_actions_env.py` — merge upstream changes

**Phase exit criteria:** `pytest tests/pytests/` passes with no regressions in existing Azure deploy tests.

---

### Phase 3 — Add New Ubuntu Deploy Scripts
Write camera-storage-viewer-specific **wrappers/adaptations** — do NOT blindly copy files from `_upstream`.
Each new file imports or delegates to `_upstream` internals where possible and overrides only the repo-specific defaults.
After Phase 8 removes `_upstream`, the implementations must stand alone (i.e. wrappers that import from `_upstream` must inline what they need before deletion).

- [ ] P3.1 Write `scripts/deploy/portainer_helpers.py` — copy upstream as-is (pure helpers, no repo-specific defaults)
- [ ] P3.2 Write `scripts/deploy/caddy_register.py` — copy upstream as-is (pure helpers)
- [ ] P3.3 Write `scripts/deploy/ubuntu_deploy.py` — thin wrapper over upstream logic with camera-storage-viewer defaults:
  - `PORTAINER_STACK_NAME` default → `camera-storage-viewer` (not `protected-container`)
  - `UBUNTU_REMOTE_DIR` default → `/opt/camera-storage-viewer`
  - All other resolution logic unchanged from upstream
- [ ] P3.4 Write `scripts/deploy/ubuntu_deploy_proxy.sh` — copy upstream as-is (env-driven, no hardcoded names)
- [ ] P3.5 Write `scripts/deploy/build_push.sh` — copy upstream as-is (fully env-driven)

**Phase exit criteria:** `python scripts/deploy/ubuntu_deploy.py --help` runs without import errors.

---

### Phase 4 — Add Docker / Compose Files for Ubuntu
- [ ] P4.1 Create `docker/docker-compose.ubuntu.yml` — Ubuntu override (entrypoint via `ubuntu_start.sh`, ENV_DIR volume)
- [ ] P4.2 Create `docker/ubuntu_start.sh` — sources `.env` / `.env.secrets` from `ENV_DIR` then execs supervisord
- [ ] P4.3 Create `docker/proxy/docker-compose.yml` — centralized Caddy proxy stack
- [ ] P4.4 Create `docker/proxy/Caddyfile` — routes `PUBLIC_DOMAIN` → camera-storage-viewer web, `portainer.<domain>` → Portainer

**Phase exit criteria:** `docker compose -f docker/docker-compose.yml -f docker/docker-compose.ubuntu.yml config` renders without errors.

---

### Phase 5 — Update Env Example Files
All `ubuntu_deploy.py` parameters that have no built-in defaults must appear in the example files so
"no-arg" operation is possible after copying the examples.

**Required additions to `env.deploy.example`:**
| Variable | Default | Notes |
|---|---|---|
| `UBUNTU_SSH_HOST` | *(required)* | No default — must be set |
| `UBUNTU_REMOTE_DIR` | `/opt/camera-storage-viewer` | Override if different layout |
| `UBUNTU_COMPOSE_FILES` | `docker/docker-compose.yml,docker/docker-compose.ubuntu.yml` | |
| `UBUNTU_SYNC_SECRETS` | `true` | Sync env files on every deploy |
| `UBUNTU_BUILD_PUSH` | `true` | Build + push APP_IMAGE before deploy |
| `PORTAINER_HTTPS_PORT` | `9943` | |
| `PORTAINER_WEBHOOK_INSECURE` | `false` | |
| `PORTAINER_STACK_NAME` | `camera-storage-viewer` | |
| `PORTAINER_ENDPOINT_ID` | `1` | |
| `CADDY_PROXY_DIR` | *(optional)* | Override default proxy path on remote |

**Required additions to `env.deploy.secrets.example`:**
| Variable | Notes |
|---|---|
| `PORTAINER_WEBHOOK_TOKEN` | Token tail from Portainer stack webhook URL |
| `PORTAINER_ACCESS_TOKEN` | Portainer API key (enables auto stack creation) |

- [ ] P5.1 Update `env.deploy.example` with Ubuntu / Portainer section
- [ ] P5.2 Update `env.deploy.secrets.example` with Portainer secrets
- [ ] P5.3 Update `.env.deploy` (real file, not committed) — add all new Ubuntu vars for local use

**Phase exit criteria:** Running `python scripts/deploy/ubuntu_deploy.py` with all Ubuntu vars in `.env.deploy` / `.env.deploy.secrets` and a reachable `UBUNTU_SSH_HOST` completes step 1 (SSH connectivity check).

---

### Phase 6 — Documentation
- [ ] P6.1 Copy `docs/deploy/UBUNTU_SERVER.md` from upstream (adapt domain/stack-name references)
- [ ] P6.2 Copy `docs/deploy/SHARED_CADDY_ROUTING.md` from upstream
- [ ] P6.3 Update `docs/deploy/ENV_SCHEMA.md` — document new Ubuntu / Portainer variables
- [ ] P6.4 Update `CHANGELOG.md` — summarise the Ubuntu deploy additions

**Phase exit criteria:** `docs/deploy/UBUNTU_SERVER.md` contains a working end-to-end guide using camera-storage-viewer names.

---

### Phase 7 — Tests
- [ ] P7.1 Add `tests/pytests/test_ubuntu_deploy.py`
  - Test `build_rsync_cmd`, `build_ssh_cmd`, SSH connectivity helpers (pure unit — no network)
  - Test `render_compose_stack_content` with a fixture compose file
  - Test `prepare_stack_content_for_portainer` strips build context + injects image
  - Test `rewrite_rendered_paths_for_remote` path substitution
  - Test `parse_boolish` with all truthy/falsy values
  - Test `main()` raises `SystemExit` when `UBUNTU_SSH_HOST` is missing
- [ ] P7.2 Add `tests/pytests/test_portainer_helpers.py`
  - Test `_extract_webhook_token` with various payload shapes
  - Test `extract_ssh_hostname` with/without `user@` prefix
  - Test `build_portainer_webhook_urls_from_token` URL construction
- [ ] P7.3 Add `tests/pytests/test_caddy_register.py`
  - Test `_domain_present` regex with already-registered domain
  - Test `_public_domain_placeholder_present` detection
  - Test idempotent no-op when domain already exists
- [ ] P7.4 Run full test suite — all passing

**Phase exit criteria:** `pytest tests/pytests/` green, including new tests.

---

### Phase 8 — Remove `_upstream` submodule

After all merges are complete and tests pass, detach from the submodule:

- [ ] P8.1 `git submodule deinit -f scripts/deploy/_upstream`
- [ ] P8.2 `git rm -f scripts/deploy/_upstream`
- [ ] P8.3 `rm -rf .git/modules/scripts/deploy/_upstream`
- [ ] P8.4 Remove the `[submodule "scripts/deploy/_upstream"]` entry from `.gitmodules` (done automatically by `git rm`)
- [ ] P8.5 Commit the removal

> Note: the `upstream` git remote (`https://github.com/beejones/protected-azure-container.git`) has already been removed (`git remote remove upstream`). Only the submodule remains, managed via `scripts/sync_deploy_upstream.py`.

**Phase exit criteria:** `scripts/deploy/_upstream/` no longer exists; `.gitmodules` is empty or removed; `git status` shows only expected changes.

---

## No-Arg Execution Contract

`python scripts/deploy/ubuntu_deploy.py` (zero CLI args) must work given:
```
# .env.deploy
UBUNTU_SSH_HOST=ronny@<server-ip>       # required, no default
APP_IMAGE=ghcr.io/beejones/camera-storage-viewer:latest

# optional (have built-in defaults):
UBUNTU_REMOTE_DIR=/opt/camera-storage-viewer
UBUNTU_COMPOSE_FILES=docker/docker-compose.yml,docker/docker-compose.ubuntu.yml
PORTAINER_STACK_NAME=camera-storage-viewer
PORTAINER_HTTPS_PORT=9943
UBUNTU_BUILD_PUSH=true

# .env.deploy.secrets
GHCR_TOKEN=<token>
PORTAINER_ACCESS_TOKEN=<api-key>   # OR PORTAINER_WEBHOOK_TOKEN=<token>
```

The script reads all values via `read_deploy_key` / `read_deploy_secret_key` before
any network or SSH action — zero flags, all config from files.

---

## Files Changed / Added Summary

| File | Action | Phase |
|---|---|---|
| `AGENT.md` | Update project context | P1.2 |
| `scripts/deploy/env_schema.py` | Merge | P2.1 |
| `scripts/deploy/validate_env.py` | Merge | P2.2 |
| `scripts/deploy/docker_compose_helpers.py` | Merge | P2.3 |
| `scripts/deploy/azure_deploy_container.py` | Merge | P2.4 |
| `scripts/deploy/azure_deploy_yaml_helpers.py` | Merge | P2.5 |
| `scripts/deploy/azure_upload_env.py` | Merge | P2.6 |
| `scripts/deploy/gh_nuke_secrets.py` | Merge | P2.7 |
| `scripts/deploy/gh_sync_actions_env.py` | Merge | P2.8 |
| `scripts/deploy/portainer_helpers.py` | Add (new) | P3.1 |
| `scripts/deploy/caddy_register.py` | Add (new) | P3.2 |
| `scripts/deploy/ubuntu_deploy.py` | Add (new) | P3.3 |
| `scripts/deploy/ubuntu_deploy_proxy.sh` | Add (new) | P3.4 |
| `scripts/deploy/build_push.sh` | Add (new) | P3.5 |
| `docker/docker-compose.ubuntu.yml` | Add (new) | P4.1 |
| `docker/ubuntu_start.sh` | Add (new) | P4.2 |
| `docker/proxy/docker-compose.yml` | Add (new) | P4.3 |
| `docker/proxy/Caddyfile` | Add (new) | P4.4 |
| `env.deploy.example` | Update | P5.1 |
| `env.deploy.secrets.example` | Update | P5.2 |
| `docs/deploy/UBUNTU_SERVER.md` | Add (new) | P6.1 |
| `docs/deploy/SHARED_CADDY_ROUTING.md` | Add (new) | P6.2 |
| `docs/deploy/ENV_SCHEMA.md` | Update | P6.3 |
| `CHANGELOG.md` | Update | P6.4 |
| `tests/pytests/test_ubuntu_deploy.py` | Add (new) | P7.1 |
| `tests/pytests/test_portainer_helpers.py` | Add (new) | P7.2 |
| `tests/pytests/test_caddy_register.py` | Add (new) | P7.3 |
| `scripts/deploy/_upstream/` | Remove submodule | P8.1–P8.5 |

---

## Key Design Decisions

1. **`ubuntu_deploy.py` default `PORTAINER_STACK_NAME`** — The upstream falls back to `remote_dir.name`
   (i.e. `protected-container`). This wrapper defaults `PORTAINER_STACK_NAME` to `camera-storage-viewer`
   and `UBUNTU_REMOTE_DIR` to `/opt/camera-storage-viewer` — both set in `.env.deploy`.

2. **`docker/docker-compose.ubuntu.yml` compose override** — Uses `ubuntu_start.sh` as entrypoint,
   mounts `ENV_DIR` (default `/opt/camera-storage-viewer`) as the env source directory.  The main
   `docker-compose.yml` remains unchanged and usable standalone for Azure/local.

3. **Proxy Caddyfile** — Uses `{$PUBLIC_DOMAIN}` placeholder + portainer subdomain.  Update the
   Portainer route to `portainer.<PUBLIC_DOMAIN>` (parameterised) instead of the hardcoded
   `portainer.zenia.eu` found in upstream.

4. **`env_schema.py` merge strategy** — Keep all local camera FTP vars; add upstream's
   `DOTENV_SECRETS` / `DOTENV_DEPLOY_SECRETS` enum entries and `SECRETS_SCHEMA` tuple.

5. **`_upstream` removal** — Done last (Phase 8) after all merges are verified. Use `git submodule deinit` + `git rm`, not a plain `rm -rf`. The `upstream` git remote has already been removed.
