# Camera Storage Viewer — Plan

## Goal
Deploy an Azure Container Instance (ACI) that:
1. Acts as an FTP endpoint for one or more cameras (starting with Reolink) to upload recordings/snapshots.
2. Provides a web UI to browse cameras and playback recordings on a timeline, with per-clip download.
3. Follows the “protected Azure container” model (cloned from https://github.com/beejones/camera-storage-viewer/):
  - secrets stored in Key Vault
  - access via Managed Identity
  - viewer authentication TBD (viewer not yet implemented; FTP-only deployment for now)
4. Stores clips and application state on **Azure Files** mounted into the container:
  - clips under `out/videos/<camera_id>/...`
  - database under `out/databases/...`
5. Uses secure transfer when possible (FTPS if the camera supports it); otherwise plain FTP with compensating controls.
6. Supports retention (e.g. auto-delete recordings older than 30 days), configurable.

Non-goals (initially):
- Real-time live view / WebRTC.
- Complex analytics (object detection, person recognition).
- Full multi-tenant SaaS (this is a single owner/operator deployment).

## Current Status (Jan 2026)
Completed (working prototype):
- FTP server container running locally and in Azure (ACI).
- Passive port range configured and proven with real camera uploads.
- Per-camera folder layouts under `/data/incoming/<camera_id>/...`.
- Deploy tooling + env schema in place; runtime env stored in Key Vault.
- Operational tooling: safe cleanup utility for `/data/incoming` in Azure.

Next (production direction):
- Harden FTP for production (transport security, auth hardening, network controls, observability).
- Build the viewer app: timeline + thumbnails + playback, inspired by the UI screenshot.
- Ingest/index: move files from `/data/incoming` into a stable library layout with metadata DB.

## Constraints / Reality Checks
- **FTP in cloud is tricky** (NAT + passive ports). We must support **PASV** with a **fixed passive port range** and expose those ports from ACI.
- Reolink FTP client typically uses plain FTP. Prefer deploying behind a locked-down network, IP allow-list, or strong per-camera credentials.
- Large video storage should not live in the container filesystem long-term; use **Azure Files** for durability (mount as `out/`).

## Proposed Architecture (MVP)
### Container Group
Keep the existing 2-container pattern and add an application process:
- (Future) Viewer/API container(s): authentication and TLS strategy TBD.
- `camera-storage-viewer`: the app processes (FTP server + web API/UI). (No code-server for this project.)

Expose ports:
- FTP: `21` + passive range (data ports)
- HTTP: `80` (optional redirect)
- FTP control: `21`
- FTP passive range: e.g. `50000-50100` (size TBD)

Notes:
- The FTP listener must bind on `0.0.0.0:21`.
- Passive ports must be explicitly opened in ACI YAML (or CLI flags) and allowed via any NSG / firewall.

### Data Flow
1. Camera uploads to FTP: `/incoming/<camera_id>/...`.
2. Upload is written to a spool directory.
3. A background ingester:
   - validates file (size > 0, known extension)
   - extracts metadata (timestamp, duration if possible)
   - moves file to storage layout: `/<storage>/<camera_id>/YYYY/MM/DD/<filename>`
  - writes/updates an index DB (SQLite for MVP)
4. Web UI queries the index DB and serves:
   - timeline view
   - playback (HTTP range requests)
   - direct download

### Storage
Primary (MVP): **Azure Files** mounted into the container.
- Mount point: `out/` (shared, durable)
- Clips layout: `out/videos/<camera_id>/YYYY/MM/DD/<filename>`
- DB layout: `out/databases/index.sqlite` (or versioned path)

Local development:
- Use the same `out/` layout on local disk via docker bind-mount.

### App Stack (Python-first)
Given the repo already uses Python deployment tooling/tests:
- Backend: **FastAPI** (API + simple HTML templates) or **Starlette**.
- FTP server: **pyftpdlib** (supports passive port range, per-user directories).
- Index DB: **SQLite** + SQLAlchemy (or raw sqlite3).
- UI: minimal server-rendered pages (Jinja2) or HTMX for MVP.

## Multi-Camera Support
Represent cameras as first-class entities:
- `camera_id` (stable slug)
- `display_name`
- `ftp_username` (unique per camera)
- `root_dir` (jail to `/incoming/<camera_id>`)

Authorization model:
- FTP: per-camera user/password; no anonymous.
- Viewer auth/TLS: TBD.

## Playback + Timeline UX (MVP)
Key UI screens:
1. **Camera list**: cards/rows, last upload time, total clips today.
2. **Camera detail**:
   - Date picker (default today)
   - Timeline bar showing clip blocks (start time + duration)
   - Click clip → playback modal or dedicated page
   - Download button

Backend API endpoints (example):
- `GET /api/cameras`
- `GET /api/cameras/{camera_id}/clips?date=YYYY-MM-DD`
- `GET /api/clips/{clip_id}`
- `GET /media/{clip_id}` (stream with Range support)

## Viewer UX (Inspired by Screenshot)

Target UI layout:
- **Left sidebar**: device list (status dot, name), quick search/filter, per-device settings.
- **Main stage**: large playback surface (video element), overlay controls (download, quality).
- **Bottom timeline**:
  - hour scale with zoom (minute/hour/day)
  - clip blocks aligned by start time
  - thumbnail strip (per clip or periodic thumbnails)
  - scrubber + current time indicator

Thumbnail strategy:
- If the camera uploads a JPEG thumbnail alongside each MP4, store them together and index both.
- If missing, generate thumbnails server-side using ffmpeg to extract a representative frame (e.g. at 1s).

Performance notes:
- Lazy-load thumbnails (only fetch what’s visible in the viewport).
- Provide smaller thumbnail sizes (e.g. 160px wide) for timeline strip.

## Security
- Strong FTP credentials (unique per camera).
- Prefer FTPS (explicit TLS) if camera supports it; if not, mitigate:
  - restrict inbound IPs (if your ISP IP is stable)
  - rotate credentials
  - store credentials in Key Vault
- FTP-only MVP; viewer authentication TBD.

## Production FTP Server (Security-First)

### Transport
- Prefer **FTPS (explicit TLS)** when supported by the camera.
  - TLS policy: TLS 1.2+, disable weak ciphers, disable TLS compression.
  - Certificate strategy:
    - public cert if the camera validates public CAs, otherwise
    - private CA / pinned cert (if camera supports), otherwise accept that some cameras can’t do validation.
- If stuck with **plain FTP** (common for cameras): treat the endpoint as hostile-network exposed and add compensating controls.

### Authentication + Authorization
- Unique credentials per camera/user.
- Deny anonymous.
- Enforce strong password policy (length + randomness); store hashes only (bcrypt).
- Lockout/backoff on failed logins (per IP and per username).
- Least privilege filesystem jail (each user restricted to its own folder).

### Network Controls
- Keep PASV range minimal for ACI (5 public ports total constraint); document the trade-offs.
- Prefer private networking for production:
  - ACI in VNet + VPN (WireGuard/OpenVPN) so the FTP endpoint is not public, **or**
  - move to a platform with better ingress controls (VM/Container Apps) if public FTP is unavoidable.
- Optional IP allow-list (best-effort; depends on camera network/IP stability).

### Hardening + Ops
- Run processes as non-root where practical.
- Tight container permissions (read-only rootfs if feasible; write only under `/data`).
- Connection limits (max concurrent connections per user/IP).
- Audit logs: uploads, deletes, auth failures, and source IP.
- Alerts (basic): repeated failed logins, disk usage thresholds, upload failures.

### Data Safety
- Write uploads to a spool/temporary name, then atomically move into place when complete.
- Verify file integrity basics (size > 0, expected extensions).
- Retention and cleanup jobs must be idempotent and safe by default.

## Ingestion + Indexing + Thumbnails

Goal: Make uploads immediately discoverable in the viewer with fast browsing and a timeline UI.

Ingestion:
- Define and enforce an upload path convention, e.g. `/data/incoming/{camera_id}/{YYYY}/{MM}/{DD}/{timestamp}.mp4`.
- Support optional sidecar thumbnail upload, e.g. `{timestamp}.jpg`.
- Validate uploads (size limits, allowed extensions; ignore temp/partial names).

Library layout (post-ingest):
- Move completed files into a stable library: `/data/out/videos/{camera_id}/{YYYY}/{MM}/{DD}/{timestamp}.mp4`.
- Store thumbnails either next to the clip or in `/data/out/thumbnails/{camera_id}/{YYYY}/{MM}/{DD}/{timestamp}.jpg`.

Indexing:
- Periodic indexer scans the library and stores metadata (camera_id, start_time, duration, file size, paths).
- Extract duration via ffprobe.
- Record a stable clip id (DB id; avoid using raw paths as ids in the UI/API).

Thumbnails:
- If `{timestamp}.jpg` exists, use it.
- Else generate and cache (ffmpeg extract at ~1s; optionally also a mid-clip frame).
- Provide variants: a small one for the timeline strip and a larger one for hover/preview.

## Retention
- A scheduled job deletes old clips based on `RETENTION_DAYS` (e.g. 30).
- Deletion must update the index DB and remove files from `out/videos`.

## Deployment & Repo Integration
We will reuse and extend:
- `scripts/deploy/*` to:
  - open additional ports (21 + passive range)
  - optionally provision a storage account/container
  - optionally provision Key Vault secrets for camera FTP creds
- `docs/` to add:
  - camera configuration guide (Reolink FTP settings)
  - networking requirements (PASV ports)
  - storage options (Blob vs Files)
- `tests/pytests` to cover:
  - env schema validation for new keys
  - path sanitization + per-camera jail behavior
  - clip indexing logic and time parsing

## Env / Schema Additions (Draft)
Runtime (`.env`):
- `APP_BASE_URL` (for generating links)
- `WEB_BIND_HOST=0.0.0.0`
- `WEB_PORT=8081` (internal)
- `FTP_BIND_HOST=0.0.0.0`
- `FTP_PORT=21`
- `FTP_PASSIVE_PORT_MIN=50000`
- `FTP_PASSIVE_PORT_MAX=50100`
- `FTP_PUBLIC_HOST=<public ip or dns>` (for PASV responses)
- `CAMERAS_CONFIG_JSON` (map of camera ids → creds/dirs) **or** one-secret-per-camera

Deploy (`.env.deploy`):
- `ACI_EXPOSED_PORTS=80,443,21,50000-50100` (shape TBD to fit existing scripts)

## Risks / Open Questions
- **FTP passive mode**: what exact settings does the Reolink firmware require?
  - confirm if it supports PASV vs PORT only
  - confirm if it needs explicit “PASV address”
- **FTPS support**: confirm whether the camera supports FTPS and which mode (explicit vs implicit).
- ACI public IP stability (static vs changing on redeploy).
- File format compatibility and whether recordings are uploaded as MP4 chunks or proprietary.

## Milestones
### Phase 0 — Discovery (1–2 hours)
- [x] Verify camera FTP capabilities: passive mode, directory behavior, filename pattern.
- [x] Decide storage direction for MVP: Azure Files mounted into the container.

### Phase 1 — Local MVP (1–2 days)
- [x] Implement FTP server + per-camera users and jailed directories.
- [ ] Implement ingest/index + minimal web UI timeline.
- [ ] Add tests for indexing and path safety.

### Phase 2 — Azure MVP (1–2 days)
- [x] Update deploy scripts/runtime to expose FTP + passive ports.
- [ ] Add docs for Reolink settings.
- [x] Validate uploads from camera to ACI.

### Phase 2b — Viewer UX MVP (1–3 days)
- [ ] Implement camera list + camera detail pages matching the target layout (sidebar + stage + timeline).
- [ ] Implement clip playback with HTTP Range support.
- [ ] Implement timeline thumbnails (uploaded or generated) with lazy-loading.

### Phase 3 — Durable Storage + Scaling (later)
- Optional: move clips to Azure Blob; serve via SAS URLs or proxy streaming.
- Retention policies + housekeeping.

## Acceptance Criteria (MVP)
- Camera can upload via FTP to the public ACI endpoint.
- Uploads are attributed to the correct camera and stored under a stable layout.
- Web UI lists cameras and shows clips on a per-day timeline.
- Clip playback works in-browser and clip download works.
