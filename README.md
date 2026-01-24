# Camera Storage Viewer

This repo is evolving into an Azure-hosted camera recorder + viewer.

Prototype (this step): an **FTP server** you can point one or more cameras at (starting with Reolink), so we can validate uploads and networking (PASV, port ranges) end-to-end.

## Quick Start (Local FTP Prototype)

1) Create `.env`:

```bash
cp env.example .env
```

2) Set the required values in `.env`:
- `BASIC_AUTH_HASH` (for the Caddy-protected web endpoint)
- FTP users (choose one):
	- single camera: `FTP_USERNAME`, `FTP_PASSWORD`, `FTP_CAMERA_ID`
	- multi-camera: `FTP_USERS_JSON`

3) Start containers:

```bash
docker compose up --build
```

This starts the app container (FTP server + internal services). The TLS proxy is disabled by default for the prototype.

To also start the Caddy TLS proxy (requires `BASIC_AUTH_HASH` in `.env`):

```bash
docker compose --profile web up --build
```

FTP will be available at `localhost:21`.

Uploaded files land under:
- `out/incoming/<camera_id>/...`

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
- a passive range (default in this repo): `50000-50100`

Locally, [docker-compose.yml](docker-compose.yml) publishes these ports.
In Azure, the ACI container group must expose the same ports.

If PASV uploads fail in Azure, you usually need to set:
- `FTP_PUBLIC_HOST` to your public DNS name or IP (so PASV replies contain a reachable address)

## Multi-Camera Configuration

Recommended: `FTP_USERS_JSON` in `.env` as a JSON list:

```text
FTP_USERS_JSON=[{"camera_id":"front","username":"front","password":"..."},{"camera_id":"back","username":"back","password":"..."}]
```

Each user is jailed to its own folder: `out/incoming/<camera_id>`.

## Where This Is Going

Next steps are documented in [planning/camera-storage-viewer-plan.md](planning/camera-storage-viewer-plan.md):
- ingest/index recordings into `out/videos/<camera_id>/...`
- timeline playback + downloads in a web UI
- retention policy (e.g. delete recordings older than 30 days)

## License

MIT License - see [LICENSE](LICENSE)
