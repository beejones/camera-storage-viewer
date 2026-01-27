import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from scripts.deploy import deploy_hooks
from scripts.deploy import azure_deploy_container

# Mock deploy hooks for testing
class HookRecorder:
    def __init__(self):
        self.log = []

    def pre_validate_env(self, ctx):
        self.log.append("pre_validate_env")
        ctx.env["TEST_VAR"] = "injected"

    def post_validate_env(self, ctx):
        self.log.append("post_validate_env")

    def build_deploy_plan(self, ctx, plan):
        self.log.append("build_deploy_plan")
        # Verify metadata exists
        assert plan.deploy_mode == "full"
        assert plan.deploy_role == "app"
        assert plan.compose_service_name == "app"
        
        plan.extra_metadata["tested"] = True
        plan.app_cpu = 9.9  # Override

    def pre_render_yaml(self, ctx, plan):
        self.log.append("pre_render_yaml")

    def post_render_yaml(self, ctx, plan, yaml_text):
        self.log.append("post_render_yaml")
        return yaml_text + "\n# Patched by hook"

    def pre_az_apply(self, ctx, plan, yaml_path):
        self.log.append("pre_az_apply")

    def post_deploy(self, ctx, plan, result):
        self.log.append("post_deploy")

    def on_error(self, ctx, exc):
        self.log.append(f"on_error: {type(exc).__name__}")


@pytest.fixture
def mock_hooks_module(monkeypatch):
    hooks = HookRecorder()
    mock_mod = MagicMock()
    mock_mod.get_hooks.return_value = hooks
    
    # Register dummy_module in sys.modules so importlib can find it
    with patch.dict(sys.modules, {"dummy_module": mock_mod}):
        yield hooks

@pytest.fixture
def base_mocks(monkeypatch, tmp_path):
    # Mock Azure login check/other utils
    monkeypatch.setattr(azure_deploy_container, "az_logged_in", lambda: True)
    monkeypatch.setattr(azure_deploy_container, "ensure_infra", MagicMock())
    monkeypatch.setattr(azure_deploy_container, "get_storage_key", lambda account, rg: "dummy-key")
    monkeypatch.setattr(azure_deploy_container, "get_identity_details", lambda name, rg: ("/id", "client-id", "tenant-id"))
    monkeypatch.setattr(azure_deploy_container, "ensure_oidc_app_and_sp", lambda display_name: "client-id")
    monkeypatch.setattr(azure_deploy_container, "ensure_oidc_app_role_assignment", MagicMock())
    
    def mock_az_command(args, **kwargs):
        if "container" in args and "show" in args:
            return None
        return MagicMock()

    monkeypatch.setattr(azure_deploy_container, "run_az_command", mock_az_command)
    monkeypatch.setattr(azure_deploy_container, "docker_pull", MagicMock())
    monkeypatch.setattr(azure_deploy_container, "docker_push", MagicMock())
    monkeypatch.setattr(azure_deploy_container, "docker_build", MagicMock())
    monkeypatch.setattr(azure_deploy_container, "docker_login", MagicMock())
    
    monkeypatch.setattr(azure_deploy_container, "kv_secret_get", lambda *args, **kwargs: None)
    monkeypatch.setattr(azure_deploy_container, "kv_secret_set", MagicMock())
    monkeypatch.setattr(azure_deploy_container.compose_helpers, "load_docker_compose_config", lambda root: {"services": {
        "app": {"image": "nginx:latest", "x-deploy-role": "app"},
    }})

    (tmp_path / ".env.deploy").write_text("AZURE_RESOURCE_GROUP=rg\nAZURE_LOCATION=loc\nPUBLIC_DOMAIN=dom.com\nACME_EMAIL=me@test.com\nAPP_IMAGE=nginx:latest\n")
    (tmp_path / ".env").write_text("BASIC_AUTH_HASH=$2b$14$dummyhash")

def test_deploy_hooks_flow_basic(mock_hooks_module, base_mocks, monkeypatch, tmp_path):
    test_args = [
        "azure_deploy_container.py",
        "--env-file", str(tmp_path / ".env.deploy"),
        "--no-validate-dotenv",
        "--no-set-vars-secrets",
        "--no-upload-env",
        "--no-persist-to-keyvault",
        "--image", "nginx:latest", 
        "--container-name", "test-cont",
        "--hooks-module", "dummy_module",
        "--no-publish",
        "--no-prefetch-images",
    ]
    
    with patch("tempfile.NamedTemporaryFile") as mock_temp:
        mock_file = MagicMock()
        mock_temp.return_value.__enter__.return_value = mock_file
        mock_file.name = str(tmp_path / "deploy.yaml")
        
        azure_deploy_container.main(argv=test_args[1:], repo_root_override=tmp_path)
        
        # Verify YAML content was written
        args, _ = mock_file.write.call_args
        written_yaml = args[0]
        assert "# Patched by hook" in written_yaml
        assert "cpu: 9.9" in written_yaml

    assert "pre_validate_env" in mock_hooks_module.log
    assert "build_deploy_plan" in mock_hooks_module.log
    assert "pre_render_yaml" in mock_hooks_module.log
    assert "post_render_yaml" in mock_hooks_module.log
    assert "pre_az_apply" in mock_hooks_module.log
    assert "post_deploy" in mock_hooks_module.log

def test_deploy_hooks_flow_with_env_validation(mock_hooks_module, base_mocks, monkeypatch, tmp_path):
    test_args = [
        "azure_deploy_container.py",
        "--env-file", str(tmp_path / ".env.deploy"),
        "--validate-dotenv",
        "--no-set-vars-secrets",
        "--no-upload-env",
        "--no-persist-to-keyvault",
        "--image", "nginx:latest", 
        "--container-name", "test-cont",
        "--hooks-module", "dummy_module",
        "--no-publish",
        "--no-prefetch-images",
    ]
    
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-id")
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-id")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-id")
    
    with patch("tempfile.NamedTemporaryFile") as mock_temp:
        mock_file = MagicMock()
        mock_temp.return_value.__enter__.return_value = mock_file
        mock_file.name = str(tmp_path / "deploy.yaml")
        
        azure_deploy_container.main(argv=test_args[1:], repo_root_override=tmp_path)

    assert "pre_validate_env" in mock_hooks_module.log
    assert "post_validate_env" in mock_hooks_module.log
