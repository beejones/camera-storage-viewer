# Storage Manager Registration — Replace RETENTION_DAYS

Replace the stub `RETENTION_DAYS` variable with proper storage manager registration rules via Docker Compose labels. The upstream storage manager (in `scripts/deploy/_upstream/docker/storage-manager/`) reads these labels and applies cleanup algorithms automatically.

## Task Checklist

### Phase 0 — Cleanup (remove RETENTION_DAYS)
- [x] Remove `RETENTION_DAYS=7` from `.env`
- [x] Remove `RETENTION_DAYS=30` from `env.example`
- [x] Remove `RETENTION_DAYS` from `VarsEnum` in `scripts/deploy/env_schema.py`
- [x] Remove `RETENTION_DAYS` `EnvKeySpec` entry from `RUNTIME_SCHEMA` in `scripts/deploy/env_schema.py`

### Phase 1 — Add Storage Manager Compose Labels
- [x] Add `labels` to the `ftp` service in `docker/docker-compose.yml`:
  - Rule 0: `incoming/vooraan` → `remove_before_date`, `max_age_days: 5`
  - Rule 1: whole data volume cap → `max_size`, 100 GiB

### Phase 2 — Verification
- [x] Run full test suite: `source .venv/bin/activate && pytest tests/pytests/ -v` → 108 passed
- [x] Confirm `docker compose config` parses without error

## Phase Exit Criteria

| Phase | Exit Criteria |
|-------|---------------|
| 0 | `RETENTION_DAYS` is gone from all files; `pytest tests/pytests/ -v` green. |
| 1 | `docker compose config` shows labels on the `ftp` service. |
| 2 | Full test suite green; no regressions. |

---

## Proposed Changes

### Phase 0 — Remove RETENTION_DAYS

#### [MODIFY] `.env`
```diff
-# Retention (not implemented yet; reserved)
-RETENTION_DAYS=7
```

#### [MODIFY] `env.example`
```diff
-# Retention policy
-RETENTION_DAYS=30
```

#### [MODIFY] `scripts/deploy/env_schema.py`
- Remove `RETENTION_DAYS = "RETENTION_DAYS"` from `VarsEnum`.
- Remove the `EnvKeySpec(key=VarsEnum.RETENTION_DAYS, ...)` block from `RUNTIME_SCHEMA`.

---

### Phase 1 — Storage Registration Labels in `docker/docker-compose.yml`

Add `labels` to the **`ftp`** service:

| # | Path | Algorithm | Key Params | Description |
|---|------|-----------|------------|-------------|
| 0 | `/incoming` | `remove_before_date` | `max_age_days: 5` | Keep incoming/vooraan footage 5 days |
| 1 | `/` | `max_size` | `max_bytes: 107374182400` (100 GiB) | Hard cap on total data volume |

```yaml
labels:
  # Rule 0: prune incoming/vooraan clips older than 5 days
  storage-manager.0.volume: "camera-storage-viewer_data"
  storage-manager.0.path: "/incoming"
  storage-manager.0.algorithm: "remove_before_date"
  storage-manager.0.max_age_days: "5"
  storage-manager.0.description: "Keep incoming/vooraan footage 5 days"
  # Rule 1: global volume hard cap at 100 GiB (deletes oldest first)
  storage-manager.1.volume: "camera-storage-viewer_data"
  storage-manager.1.path: "/"
  storage-manager.1.algorithm: "max_size"
  storage-manager.1.max_bytes: "107374182400"
  storage-manager.1.sort_by: "mtime"
  storage-manager.1.description: "Hard cap: total data volume ≤ 100 GiB"
```

> **Note on volume name**: `camera-storage-viewer_data` follows Docker Compose's `<project>_<volume>` convention. Since the `ftp` service currently uses a bind-mount (`./out:/data`), these labels document intent for the storage manager once the data is backed by a named volume on Ubuntu/Portainer. The `path` values are relative to the volume root: `/incoming` targets the `incoming/` subdirectory where the FTP server writes camera uploads.

---

## Verification Plan

### Automated Tests
```bash
source .venv/bin/activate && pytest tests/pytests/ -v
```

Key tests:
- `test_env_schema.py` — must pass without `RETENTION_DAYS` in schema
- `test_no_raw_env_keys.py` — static check on deploy scripts (no changes expected)

### Manual Verification
1. Confirm `RETENTION_DAYS` is gone from `.env` and `env.example`.
2. Run `docker compose config` and verify labels appear on the `ftp` service.
