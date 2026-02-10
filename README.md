# Camera Storage Viewer

This repo is evolving into an Azure-hosted camera recorder + viewer.


## Deploy engine integration

This repo integrates the upstream deploy engine **protected-azure-container** as a pinned git submodule at `scripts/deploy/_upstream`.

- Use the wrapper scripts in `scripts/deploy/` as the stable entrypoints.
- Keep repo-specific behavior in `scripts/deploy/deploy_customizations.py` (hooks).

After cloning, initialize the submodule:

```bash
git submodule update --init --recursive
```

Or via the helper:

```bash
python scripts/sync_deploy_upstream.py --init
```

To update the pinned upstream engine version later:

```bash
python scripts/sync_deploy_upstream.py --update
git add scripts/deploy/_upstream
git commit -m "Update deploy engine submodule"
```

CI and deploy workflows must checkout submodules (GitHub Actions: `actions/checkout@v4` with `submodules: recursive`).

## Upstream (Project Origin)

This repository started as a clone/fork of:
- https://github.com/beejones/protected-azure-container

It keeps the same overall deployment approach (Azure Container Instances + a small TLS proxy sidecar), but adapts it to the camera FTP ingest + viewer use-case.

### How to pull updates from upstream

Note: with the submodule integration, **deploy-engine updates should be done by updating the submodule** (see above). Merging upstream `main` is still possible, but is more likely to cause conflicts.

One-liner (merge upstream `main` into your current branch):

```bash
git fetch upstream main && git merge upstream/main
```

1) Add an `upstream` remote (one-time):

```bash
git remote add upstream https://github.com/beejones/protected-azure-container.git
git remote -v
```

2) Fetch upstream branches/tags:

```bash
git fetch upstream --prune
```

3) Update your current branch.

Option A: merge upstream `main` into your current branch (preserves history):

```bash
git merge upstream/main
```

Option B: rebase your work on top of upstream `main` (cleaner linear history, but rewrites commits):

```bash
git rebase upstream/main
```

4) Resolve any conflicts, run tests, and push your branch:

```bash
make test
git push
```

## Development: Python env + tests

The tests require Python dependencies like `fastapi` and `pytest-asyncio`.
If you run `pytest` from your global Python, you may see collection errors like:

- `ModuleNotFoundError: No module named 'fastapi'`

Recommended:

```bash
make test
```

Or explicitly run pytest via the repo virtualenv:

```bash
./.venv/bin/python -m pytest -q
```

Prototype (this step): an **FTP server** you can point one or more cameras at (starting with Reolink), so we can validate uploads and networking (PASV, port ranges) end-to-end.

## Quick Start (Local FTP Prototype)

1) Create `.env`:

```bash
cp env.example .env
```

2) Set the required values in `.env`:
- FTP users (choose one):
	- single camera: `FTP_USERNAME`, `FTP_PASSWORD`, `FTP_CAMERA_ID`
	- multi-camera: `FTP_USERS_JSON`

3) Start containers:

```bash
docker compose up --build
```

This starts:
- the FTP server container (`ftp`)
- the viewer backend API container (`web`) on `http://localhost:8081`

Optional (recommended for a nicer local URL):
- the Caddy reverse proxy (`caddy`) on `http://localhost/` (port 80)

Note: The repo builds a single app image (FTP + viewer). docker-compose runs different commands for `ftp` vs `web`.

If you only want FTP:

```bash
docker compose up --build ftp
```

FTP will be available at `localhost:21`.

Viewer API will be available at `http://localhost:8081`.

Viewer UI will be available at `http://localhost:8081/`.

If you use the Caddy proxy (default in docker-compose), the Viewer UI is also available at:
- `http://localhost/`

Uploaded files land under:
- `out/incoming/<camera_id>/...`

Note: some cameras can be configured with an absolute "server directory" like `/data/incoming/<camera_id>`.
When used with this server (which jails each user to `out/incoming/<camera_id>`), that can accidentally create
a duplicated nested tree like `out/incoming/<camera_id>/data/incoming/<camera_id>/...`.

## Ingest (Move uploads into library)

Uploads initially land under `out/incoming/`. The viewer can index those directly, but for a stable library layout you can ingest them into:
- `out/videos/<camera_id>/YYYY/MM/DD/...`

Dry-run:

```bash
python3 scripts/ingest_once.py --out-dir ./out
```

Apply moves (default skips files newer than 60s):

```bash
python3 scripts/ingest_once.py --out-dir ./out --apply
```

## Cleanup: remove empty per-camera `data/` directories

Some cameras try to `CWD /data` while logged into a jailed per-camera FTP homedir.
That can create an extra empty directory like:
- `out/incoming/<camera_id>/data/`

Dry-run:

```bash
python3 scripts/cleanup_empty_camera_data_dirs.py --out-dir /data
```

Apply deletions:

```bash
python3 scripts/cleanup_empty_camera_data_dirs.py --out-dir /data --apply
```

## Viewer API (Local)

List cameras:

```bash
curl http://localhost:8081/api/cameras
```

List clips for a day (UTC date):

```bash
curl "http://localhost:8081/api/cameras/<camera_id>/clips?date=2026-01-24"
```

Stream a clip:

```bash
curl -I http://localhost:8081/media/<clip_id>
```

Optional auth:
Thumbnails:
- If a sidecar image exists next to a clip (e.g. `clip1.jpg` next to `clip1.mp4`), the API serves it.
- Otherwise, the `web` container can generate thumbnails with ffmpeg when `THUMBNAIL_GENERATION=true`.

### Test Upload (without a camera)

Example using `curl`:

```bash
echo "hello" > /tmp/test.txt
curl -T /tmp/test.txt ftp://$FTP_USERNAME:$FTP_PASSWORD@localhost:21/
```

If you use `FTP_USERS_JSON`, pick one user/password from that list.

## FTP Networking Notes (Important)

FTP requires:
- control port `21`
- a passive range (default in this repo): `50000-50003`

Locally, [docker-compose.yml](docker-compose.yml) publishes these ports.
In Azure, the ACI container group must expose the same ports.

Important: Azure Container Instances limits a container group to **5 public ports total**, so the passive range must be small (control port 21 + up to 4 passive ports).

If PASV uploads fail in Azure, you usually need to set:
- `FTP_PUBLIC_HOST` to your public DNS name or IP (so PASV replies contain a reachable address)

## Azure Storage Cleanup

If you need to delete old uploads stored in Azure (the Azure Files share mounted at `/data` in ACI), use:
- [scripts/deploy/azure_storage_cleanup.py](scripts/deploy/azure_storage_cleanup.py)

This script runs via `az container exec` and deletes files from the mounted share (persistent), not just from the container filesystem.

Safety:
- Default is **dry-run** (prints what would be deleted)
- Add `--apply` to actually delete
- Empty directory cleanup is enabled by default; use `--no-delete-empty-dirs` to keep empty folders.

Dry-run: list files under `/data/incoming` older than a cutoff:

```bash

python3 scripts/deploy/azure_storage_cleanup.py \
	--resource-group camera-storage-viewer-rg \
	--before-date 2026-01-01
```

Apply deletion:

```bash
python3 scripts/deploy/azure_storage_cleanup.py \
	--resource-group camera-storage-viewer-rg \
	--before-date 2026-01-01 \
	--apply
```

Delete everything under `/data/incoming`:

```bash
python3 scripts/deploy/azure_storage_cleanup.py \
	--resource-group camera-storage-viewer-rg \
	--all \
	--apply
```

## Multi-Camera Configuration

Recommended: `FTP_USERS_JSON` in `.env` as a JSON list:

```text
FTP_USERS_JSON=[{"camera_id":"front","username":"front","password":"..."},{"camera_id":"back","username":"back","password":"..."}]
```

Each user is jailed to its own folder: `out/incoming/<camera_id>`.

## Security Notes (Read This)

- Anyone who has valid FTP credentials for a user can read/list/download/delete files **inside that user's FTP home directory**.
	In other words: the credentials *do* grant access to the uploaded files for that camera/user.
- With per-camera users (`FTP_USERS_JSON`), a user is restricted to its own folder (they should not be able to browse other cameras’ folders).
- This prototype is **plain FTP** (no TLS), so usernames/passwords and file contents may be observable on the network path.
	If you need confidentiality/integrity in transit, use FTPS/SFTP or place the service behind a secure tunnel/VPN.

## Where This Is Going

Next steps are documented in [planning/camera-storage-viewer-plan.md](planning/camera-storage-viewer-plan.md):
- ingest/index recordings into `out/videos/<camera_id>/...`
- timeline playback + downloads in a web UI
- retention policy (e.g. delete recordings older than 30 days)

## Deployment Customization

Downstream consumers can customize the deployment process (e.g., override images, resources, or patch YAML) using **Deployment Hooks**. This prevents the need to maintain a fork with modified core scripts.

See: [docs/deploy/HOOKS.md](docs/deploy/HOOKS.md)

## Debugging deploys: restart policy (ACI)

By default, Azure Container Instances restarts the container group when a container exits non-zero (`restartPolicy: OnFailure`), which can make debugging CrashLoopBackOff noisy.

You can control this via the deploy script:

- Normal (default, restart on failure):

```bash
python3 scripts/deploy/azure_deploy_container.py --restart-policy OnFailure
```

- Debug (do not restart on failure; container stays terminated so you can inspect logs/state):

```bash
python3 scripts/deploy/azure_deploy_container.py --restart-policy Never
```

- Optional: always restart (even on clean exit):

```bash
python3 scripts/deploy/azure_deploy_container.py --restart-policy Always
```

You can also set `ACI_RESTART_POLICY` (or `AZURE_RESTART_POLICY`) in your shell instead of passing the flag.

## Migration Guide

### Renamed Variables (Jan 2026)

To support multiple containers, generic variable names have been updated:

*   **`CONTAINER_IMAGE`** → **`APP_IMAGE`**
*   **`DEFAULT_CPU_CORES`** → **`APP_CPU_CORES`**
*   (CLI) `--cpu` → `--app-cpu` (old flag still works)
*   (CLI) `--memory` → `--app-memory` (old flag still works)

### New Sidecar/Other Variables

*   `CADDY_IMAGE`, `CADDY_CPU_CORES`, `CADDY_MEMORY_GB`
*   `OTHER_IMAGE`, `OTHER_CPU_CORES`, `OTHER_MEMORY_GB` (for generic third container)

## Security Notes

### `other` Container Volume Access

If you deploy an `other` container (e.g., using `OTHER_IMAGE`), it shares the **same workspace volume** (`/home/coder/workspace`) as the main code-server container. This allows for convenient file sharing but implies that the `other` container has full read/write access to your code files. Ensure you trust the image used for the `other` container.

## License

MIT License - see [LICENSE](LICENSE)
