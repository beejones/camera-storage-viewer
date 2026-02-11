# Agent Instructions
## Security
- **Never** read .env.secrets nor .env.deploy.secrets
## Environment
- **Always** activate the virtual environment before running Python scripts:
  ```bash
  source .venv/bin/activate && python <script>
  ```
## Start server
- **Always** start the server with the following command. This command will generate logs/app.log and the terminal shows the logs which is better for debugging. Try to start/restart the server in the same terminal:
  ```bash
  source .venv/bin/activate && python server.py
  ```
## File Organization
- **Debug Scripts**: All debug and verification scripts must be created in the `debug/` directory, not the root directory.
- **Common Code**: All common code must be created in the `src/common/` directory.
- **Temporary files**: All temporary files must be created in the `out/` directory.
- **Logs**: All logs must be created in the `logs/` directory.

## Preferences
- **CSV Exports**: Use semicolon (`;`) delimiter and comma (`,`) for decimals to ensure Excel compatibility/localization.
- **PRs**: Return the report as a raw markdown file.

## Software engineering
- **Logging**: Use Python's logging module to log messages. The logging level should be set to DEBUG. Log files should be stored in the `logs/` directory. We use PREFIX: such as [STORE]:, [COLLECTOR]:, etc to categorize our logging.
- **Error Handling**: Use try-except blocks to handle errors.
- **Code Style**: Use PEP 8 style guide for Python code. String formatting should be done using f-strings.
- Use as much as possible shared code from the `src/` directory. We always delete obsolete code.

## Testing
- **Unit Tests**: Write unit tests for all functions and classes. We use tests/pytests for all pytests, debug/ for debug scripts, tests/UI for ui testing.
- **Permissions**: Don't ask for permissions to run tests.