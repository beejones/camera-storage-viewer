import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from scripts.deploy import deploy_hooks
from scripts.deploy import azure_deploy_container

# Mock hook implementation
class ComposeMetadataHook:
    def __init__(self):
        self.captured_plan = None

    def build_deploy_plan(self, ctx, plan):
        self.captured_plan = plan

@pytest.fixture
def mock_compose_hooks(monkeypatch):
    hooks = ComposeMetadataHook()
    mock_mod = MagicMock()
    mock_mod.get_hooks.return_value = hooks
    
    with patch.dict(sys.modules, {"compose_hook_module": mock_mod}):
        yield hooks

@pytest.fixture
def compose_mocks(monkeypatch, tmp_path):
    # Mock minimal Azure/Docker requirements
    monkeypatch.setattr(azure_deploy_container, "az_logged_in", lambda: True)
    monkeypatch.setattr(azure_deploy_container, "ensure_infra", MagicMock())
    monkeypatch.setattr(azure_deploy_container, "get_storage_key", lambda account, rg: "dummy-key")
    monkeypatch.setattr(azure_deploy_container, "get_identity_details", lambda name, rg: ("/id", "client-id", "tenant-id"))
    
    def mock_az_command(args, **kwargs):
        if "container" in args and "show" in args:
            return None
        return MagicMock()

    monkeypatch.setattr(azure_deploy_container, "run_az_command", mock_az_command)
    monkeypatch.setattr(azure_deploy_container, "time", MagicMock()) # Mock time.sleep
    monkeypatch.setattr(azure_deploy_container, "docker_pull", MagicMock())
    monkeypatch.setattr(azure_deploy_container, "docker_push", MagicMock())
    monkeypatch.setattr(azure_deploy_container, "docker_build", MagicMock())
    monkeypatch.setattr(azure_deploy_container, "docker_login", MagicMock())
    monkeypatch.setattr(azure_deploy_container, "kv_secret_set", MagicMock())
    
    # Mock COMPOSE config with metadata
    monkeypatch.setattr(azure_deploy_container.compose_helpers, "load_docker_compose_config", lambda root: {
        "services": {
            "web-app": {
                "image": "my-app:1.0",
                "x-deploy-role": "app",
                "command": "./start.sh --prod",
                "ports": ["8081:8080", "9000"],
                "environment": {
                    "WEB_PORT": "8080",
                    "DEBUG": "false"
                }
            },
            "proxy": {
                "image": "caddy:latest",
                "x-deploy-role": "sidecar"
            }
        }
    })

    # Setup dummy env files
    (tmp_path / ".env.deploy").write_text("AZURE_RESOURCE_GROUP=rg\nAZURE_LOCATION=loc\nPUBLIC_DOMAIN=dom.com\nACME_EMAIL=me@test.com\n")
    (tmp_path / ".env").write_text("BASIC_AUTH_HASH=hash")

def test_compose_metadata_flow_to_hooks(mock_compose_hooks, compose_mocks, tmp_path):
    test_args = [
        "azure_deploy_container.py",
        "--env-file", str(tmp_path / ".env.deploy"),
        "--no-validate-dotenv",
        "--no-set-vars-secrets",
        "--no-upload-env",
        "--hooks-module", "compose_hook_module",
        "--no-publish",
        "--no-prefetch-images",
        "--image", "nginx:latest",
    ]
    
    # Run main with mocked filesystem and az commands
    with patch("tempfile.NamedTemporaryFile") as mock_temp:
        mock_file = MagicMock()
        mock_temp.return_value.__enter__.return_value = mock_file
        mock_file.name = str(tmp_path / "deploy.yaml")
        
        azure_deploy_container.main(argv=test_args[1:], repo_root_override=tmp_path)
    
    # Verify the hook captured the correctly populated plan
    plan = mock_compose_hooks.captured_plan
    assert plan is not None
    assert plan.compose_service_name == "web-app"
    assert plan.web_command == ["sh", "-lc", "./start.sh --prod"]
    # Ports: 8080 (from 8081:8080) and 9000
    assert 8080 in plan.app_ports
    assert 9000 in plan.app_ports
    # Extra env
    assert plan.extra_env.get("WEB_PORT") == "8080"
