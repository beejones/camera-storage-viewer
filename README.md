# Camera Storage Viewer

This repo is evolving into an Azure-hosted camera recorder + viewer.

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

This starts the FTP server container.

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


If you use `FTP_USERS_JSON`, pick one user/password from that list.
Important: Azure Container Instances limits a container group to **5 public ports total**, so the passive range must be small (control port 21 + up to 4 passive ports).

If PASV uploads fail in Azure, you usually need to set:
- `FTP_PUBLIC_HOST` to your public DNS name or IP (so PASV replies contain a reachable address)

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

## License

MIT License - see [LICENSE](LICENSE)
