from __future__ import annotations

from pathlib import Path


def test_env_secrets_slimmed_then_restored(tmp_path: Path) -> None:
    # Import locally to avoid any global state during collection.
    from scripts.deploy.deploy_customizations import CameraStorageViewerHooks

    repo_root = tmp_path
    secrets_path = repo_root / ".env.secrets"
    secrets_path.write_text(
        "\n".join(
            [
                "# full secrets",
                "BASIC_AUTH_HASH=$2b$12$abcdefghijklmnopqrstuv0123456789ABCDEFGHijklmnopqrstuv",
                "APP_SECRET=secret",
                "FTP_USERS_JSON=[{\"camera_id\":\"front\",\"username\":\"u\",\"password\":\"p\"}]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    class Ctx:
        def __init__(self) -> None:
            self.env = {}
            self.args = type("Args", (), {"upload_env_raw": False})()
            self.repo_root = repo_root

    ctx = Ctx()
    hooks = CameraStorageViewerHooks()

    hooks.pre_validate_env(ctx)

    # During validation, the file should be slimmed (FTP_USERS_JSON removed).
    slim_text = secrets_path.read_text(encoding="utf-8")
    assert "FTP_USERS_JSON" not in slim_text
    assert "BASIC_AUTH_HASH" in slim_text

    hooks.post_validate_env(ctx)

    # After validation, the full file should be restored.
    restored_text = secrets_path.read_text(encoding="utf-8")
    assert "FTP_USERS_JSON" in restored_text
