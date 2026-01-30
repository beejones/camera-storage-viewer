## Plan: Harden SQLite on Azure Files

Your current failure mode is very consistent with SQLite + Azure Files (SMB) issues: the app forces `PRAGMA journal_mode=WAL` in [src/viewer_db.py](src/viewer_db.py#L43-L49), and the web layer triggers DB writes (full re-index) on essentially every request in [src/web_api.py](src/web_api.py#L182-L352). WAL relies on extra `-wal`/`-shm` files + locking semantics that often behave poorly on network file shares, and the “index-on-every-request” pattern increases contention even in a single-replica deployment (FastAPI sync handlers run in a threadpool, so requests can overlap).

**Decision recap (from your answers):** DB stays persistent on `/data`, you run single replica, TTL refresh is OK, and if the DB is bad you want auto-rebuild and keep serving.

**Steps**
1. Add env-configurable SQLite settings (safe defaults for Azure Files)
   - Update config loading in [src/config.py](src/config.py) to support:
     - `SQLITE_JOURNAL_MODE` default `DELETE` (not WAL)
     - `SQLITE_SYNCHRONOUS` default `FULL`
     - `SQLITE_BUSY_TIMEOUT_MS` default `5000` (or similar)
     - `INDEX_REFRESH_TTL_SECONDS` default `30` (or `60`)
     - `INDEX_LOCK_PATH` default `/data/databases/index.lock`
2. Replace hardcoded WAL in `viewer_db`
   - Refactor `_connect` in [src/viewer_db.py](src/viewer_db.py#L43-L49) to:
     - Apply the env-driven PRAGMAs (journal_mode/synchronous/busy_timeout)
     - Add a small retry/backoff wrapper for transient `sqlite3.OperationalError` like “unable to open database file”
3. Serialize “index refresh” writes and stop doing them on every request
   - Add a file lock around `update_index_from_storage` in [src/viewer_db.py](src/viewer_db.py#L123-L165) using the `INDEX_LOCK_PATH` so concurrent requests don’t write simultaneously (even within one process/threadpool).
   - Add a TTL gate so `update_index_from_storage` becomes a “refresh-if-stale” operation (e.g., skip if refreshed in last N seconds). Persist “last refresh” in a small sidecar file under `/data/databases/` so it survives restarts and works across threads.
4. Auto-rebuild on corruption, but not on every transient open failure
   - When `_connect`/`init_db` hits corruption-indicative errors (e.g., “database disk image is malformed” or failing `PRAGMA integrity_check`), move `index.sqlite` aside to `index.sqlite.corrupt-YYYYmmdd-HHMMSS` and recreate from storage scan.
   - For “unable to open database file”, do a couple retries first; only rebuild if it’s clearly corruption rather than a mount/permission issue.
5. Make web handlers call the new refresh behavior
   - Update the endpoints in [src/web_api.py](src/web_api.py#L182-L352) to call the “refresh-if-stale” function (instead of unconditional full refresh) so read-heavy traffic doesn’t hammer the DB.
6. Update env schema + docs
   - Add the new env vars to the env schema tooling in [scripts/deploy/env_schema.py](scripts/deploy/env_schema.py) (and any validation) and document them in [docs/deploy/ENV_SCHEMA.md](docs/deploy/ENV_SCHEMA.md).
7. Tests
   - Extend/adjust [tests/pytests/test_web_api_contract.py](tests/pytests/test_web_api_contract.py#L44-L63) (and/or add a unit test) to assert that repeated API hits within TTL don’t re-index multiple times (can be done by monkeypatching the refresh function or checking the sidecar “last refresh” file behavior).

**Verification**
- Run `pytest -q` (at minimum: `pytest -q tests/pytests/test_web_api_contract.py`).
- Local smoke test: start the web service, hit `GET /api/cameras` repeatedly, confirm DB files are stable (no `-wal`/`-shm` if using `DELETE`), and confirm it no longer performs a full scan on every request.
- Azure check: restart the container a few times and confirm the DB opens reliably and indexing resumes (with lock + TTL).

**Decisions**
- Chose `journal_mode=DELETE` + `synchronous=FULL` by default to be Azure Files-safe.
- Chose file lock + TTL refresh to prevent concurrent writers and reduce write frequency while keeping the DB persistent on `/data`.
- Chose “auto-rebuild with backup” to preserve uptime and allow later forensic inspection.

If you approve this plan, I’ll hand it off for implementation (it’ll touch mainly [src/viewer_db.py](src/viewer_db.py) and [src/web_api.py](src/web_api.py), plus env schema/docs/tests).
