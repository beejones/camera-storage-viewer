# Plan: Multi-Camera Support

## Goal
Enable multiple cameras to upload via FTP and appear independently in the viewer with correct indexing, thumbnails, and playback.

## Assumptions
- Camera uploads land under `/data/incoming/<camera_id>/...`.
- FTP users can be configured via `FTP_USERS_JSON` or per-camera user + camera ID.
- Viewer uses SQLite index in `/data/databases/index.sqlite`.

## Scope
- FTP: multi-user mapping to per-camera directories.
- Indexing: reliable per-camera discovery from both `/data/incoming` and `/data/videos`.
- UI: camera list, per-camera timeline, and clip playback.
- Ops: deploy-time configuration and docs.

## Tasks
### 1) Runtime configuration
- [ ] Confirm schema supports multi-camera `FTP_USERS_JSON` (list/map).
- [ ] Validate `FTP_USERS_JSON` examples in `.env` docs.
- [ ] Add validation/error messages for malformed camera IDs.

### 2) FTP server behavior
- [ ] Ensure each user is jailed to its own camera folder under `/data/incoming/<camera_id>`.
- [ ] Enforce camera ID normalization and path safety.
- [ ] Confirm passive mode + public host behavior is correct in Azure.

### 3) Indexer & DB
- [ ] Verify indexer scans all camera dirs under `/data/incoming` and `/data/videos`.
- [ ] Ensure zero-byte uploads are excluded from index.
- [ ] Add test coverage for two cameras uploading concurrently.

### 4) Viewer API
- [ ] `GET /api/cameras` returns all camera IDs with latest upload.
- [ ] `GET /api/cameras/{camera_id}/clips` filters by camera.
- [ ] Confirm thumbnails and media endpoints work with multiple cameras.

### 5) UI changes
- [ ] Camera list renders multiple cameras and displays last upload timestamps.
- [ ] Switching cameras resets timeline and playback state.
- [ ] Optional: allow camera rename/display labels in UI.

### 6) Deployment & docs
- [ ] Update docs with multi-camera FTP config examples.
- [ ] Add Azure notes for multiple camera credentials (Key Vault secret usage).
- [ ] Update troubleshooting section for camera-specific upload issues.

## Acceptance Criteria
- Multiple cameras can upload simultaneously with distinct credentials.
- Viewer lists all cameras and shows correct clips per camera/date.
- Playback and thumbnail generation work per camera.
- Indexer remains stable under concurrent uploads.

## Risks / Notes
- FTP passive mode + NAT may require `FTP_PUBLIC_HOST` for each deployment.
- Some cameras may not create nested directories; server should auto-create as needed.
- Azure Files latency can impact indexing; avoid aggressive reindex loops.
