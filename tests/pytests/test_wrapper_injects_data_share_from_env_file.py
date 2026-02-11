from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.deploy import azure_deploy_container


def test_wrapper_injects_data_share_name_from_env_file(monkeypatch, tmp_path: Path):
    # Minimal upstream engine stub that records argv passed to it.
    recorded = {}

    def fake_upstream_main(argv, repo_root_override=None):
        recorded["argv"] = list(argv)
        recorded["repo_root_override"] = repo_root_override

    # Fake submodule presence and import machinery: point wrapper at the real
    # upstream dir in this repo so import works, but intercept main().
    upstream_mod = MagicMock()
    upstream_mod.main.side_effect = fake_upstream_main

    # Create env file with container name
    (tmp_path / ".env.deploy").write_text("AZURE_CONTAINER_NAME=camera-storage-viewer\n")

    # Ensure wrapper doesn't die on missing upstream by using this repo root.
    monkeypatch.setattr(azure_deploy_container.importlib.util, "spec_from_file_location", azure_deploy_container.importlib.util.spec_from_file_location)

    # Patch the import of upstream module after sys.path insert.
    monkeypatch.setitem(__import__("sys").modules, "azure_deploy_container", upstream_mod)

    # Run wrapper with explicit repo root and env-file.
    azure_deploy_container.main(argv=["--env-file", str(tmp_path / ".env.deploy")], repo_root_override=tmp_path)

    argv = recorded.get("argv")
    assert argv is not None
    assert "--data-share-name" in argv
    idx = argv.index("--data-share-name")
    assert argv[idx + 1] == "camera-storage-viewer-data"
