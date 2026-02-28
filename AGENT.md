# Agent Instructions

## 1. Critical Rules (Must Follow)
- **Security**: **NEVER** read `.env.secrets` or `.env.deploy.secrets`.
- **Environment**: **ALWAYS** run scripts within the virtual environment.
  ```bash
  source .venv/bin/activate && python <script>
  ```
- **Server Startup**: Use the dedicated startup command to ensure proper logging.
  ```bash
  source .venv/bin/activate && python run.py
  ```

## 2. Development Standards
### Code Quality
- **Style**: Follow **PEP 8**. Use **f-strings** for formatting.
- **Logging**: Use `logging` module at `DEBUG` level. Use prefixes: `[STORE]:`, `[COLLECTOR]:`, `[TRADER]:`.
- **Error Handling**: Use `try-except` blocks. Fail gracefully; avoid complex fallback mechanisms unless strictly necessary for reliability.
- **Bugs**: If bugs like uncaught exceptions are reported, we need to automatically add a test (pytest or UI) to make sure the error does not occur again.
- **Arguments**: Avoid optional arguments (`arg=None`) unless strictly necessary to prevent ambiguity/bugs. Use dict and ANY type only when necessary. Prioritize using data classes. Do strict input validation on arguments. Use strict-typing.
- **Fallback code**: Use fallbacks wisely where they cover for transient events such as API calls and timeouts. In the other cases prioritize gracefull failing
- **Cleanup**: Delete obsolete code immediately.
- **Permissions**: You have permission to run tests without asking.

# Project Context

## 1. Project Overview
**Camera Storage Viewer** is a Python application for receiving, storing and browsing camera FTP uploads.
- **FTP Server**: receives uploads from IP cameras into `/data`.
- **Web API**: FastAPI/Uvicorn — serves a thumbnail browser UI for stored footage.
- **Deploy**: supports Azure Container Instances (ACI) and Ubuntu/Portainer deployments.

## 2. File Organization
- `src/`: Application source (FTP server, ingest, thumbnails, viewer API/index).
- `scripts/deploy/`: Deploy automation (ACI + Ubuntu/Portainer).
- `docker/`: Dockerfiles, compose files, Caddy, startup scripts.
- `src/common/`: Shared utilities and core logic. **Reuse code from here whenever possible.**
- `debug/`: Verification and one-off scripts. **Do NOT pollute the root directory.**
- `out/`: Temporary output files including PR reviews and test reports.
- `logs/`: Application logs (`logs/app.log`).
- `tests/pytests/`: Unit and integration tests.
- `tests/UI/`: UI tests using playwright
- `planning/`: Planning files. Planning file must have a checkable task overview and clear phase exit criteria. The first phase in a plan should always be optimizing the module we are going to change. See (`docs/CODE_PROMPTS.md`) section ## cleanup. Also check if docs must be merged or are obsolete.
- `docs/`: Documentation. Each major topic should have a doc. 

## 3. Workflows & Preferences
### User Preferences
- **CSV Exports**: Use semicolon (`;`) delimiter and comma (`,`) for decimals (Excel-friendly).
- **PR Reviews**: Return reports as raw markdown files (e.g., `Review.md`).

### Testing Context
- **Tooling**: We use `pytest` for backend tests.
- **Philosophy**: Tests are encouraged for all new logic.