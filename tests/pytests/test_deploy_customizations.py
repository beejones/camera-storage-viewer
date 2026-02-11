import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
import yaml

from scripts.deploy import deploy_customizations
from scripts.deploy import deploy_hooks
from scripts.deploy.env_schema import VarsEnum


def _make_plan() -> deploy_hooks.DeployPlan:
    return deploy_hooks.DeployPlan(
        name="cg",
        location="loc",
        dns_label="dns",
        deploy_mode="full",
        compose_service_name="web",
        deploy_role="app",
        app_image="img",
        caddy_image="caddy:2-alpine",
        other_image=None,
        app_cpu=1.0,
        app_memory=1.0,
        caddy_cpu=0.5,
        caddy_memory=0.5,
        other_cpu=0.25,
        other_memory=0.5,
        public_domain="",
        app_port=8081,
    )


def test_pre_validate_env_injects_viewer_defaults():
    hooks = deploy_customizations.get_hooks()
    ctx = deploy_hooks.DeployContext(repo_root=Path("."), env={}, args=argparse.Namespace(service="web"))

    hooks.pre_validate_env(ctx)

    assert ctx.env[VarsEnum.OUT_DIR.value] == "/data"
    assert ctx.env[VarsEnum.WEB_PORT.value] == "8081"


def test_post_validate_env_rejects_large_ftp_passive_range():
    hooks = deploy_customizations.get_hooks()
    ctx = deploy_hooks.DeployContext(
        repo_root=Path("."),
        env={
            VarsEnum.FTP_PORT.value: "21",
            VarsEnum.FTP_PASSIVE_PORT_MIN.value: "50000",
            VarsEnum.FTP_PASSIVE_PORT_MAX.value: "50010",
        },
        args=argparse.Namespace(service="ftp"),
    )

    with pytest.raises(ValueError, match="at most 5 public ports"):
        hooks.post_validate_env(ctx)


def test_build_deploy_plan_sets_metadata_and_ports():
    hooks = deploy_customizations.get_hooks()
    ctx = deploy_hooks.DeployContext(
        repo_root=Path("."),
        env={
            VarsEnum.CADDY_IMAGE.value: "ghcr.io/example/caddy:2-alpine",
            VarsEnum.WEB_PORT.value: "8081",
            VarsEnum.FTP_PASSIVE_PORT_MIN.value: "50000",
            VarsEnum.FTP_PASSIVE_PORT_MAX.value: "50003",
        },
        args=argparse.Namespace(service="web-caddy"),
    )

    plan = _make_plan()
    hooks.build_deploy_plan(ctx, plan)

    assert plan.deploy_mode == "web-caddy"
    assert plan.caddy_image == "ghcr.io/example/caddy:2-alpine"
    assert plan.app_port == 8081
    assert plan.ftp_passive_range == "50000-50003"


def test_pre_validate_env_does_not_rewrite_deploy_env(tmp_path: Path):
    hooks = deploy_customizations.get_hooks()

    # Simulate a repo root with a deploy env that includes viewer-specific keys.
    (tmp_path / ".env.deploy").write_text(
        "\n".join(
            [
                "AZURE_RESOURCE_GROUP=rg",
                "FTP_CPU_CORES=0.25",
                "FTP_MEMORY_GB=0.5",
                "",
            ]
        ),
        encoding="utf-8",
    )

    ctx = deploy_hooks.DeployContext(
        repo_root=tmp_path,
        env={},
        args=argparse.Namespace(service="web"),
    )

    hooks.pre_validate_env(ctx)

    deploy_text = (tmp_path / ".env.deploy").read_text(encoding="utf-8")
    assert "FTP_CPU_CORES=0.25" in deploy_text
    assert "FTP_MEMORY_GB=0.5" in deploy_text
    assert not (tmp_path / ".env.deploy.full").exists()


# ── FTP Deployment Hook Tests ──────────────────────────────────────────


def _make_ftp_env() -> dict[str, str]:
    """Standard env dict with FTP-related vars for testing."""
    return {
        "FTP_PORT": "21",
        "FTP_PASSIVE_PORT_MIN": "50000",
        "FTP_PASSIVE_PORT_MAX": "50003",
        "FTP_CPU_CORES": "0.5",
        "FTP_MEMORY_GB": "1.0",
        "AZURE_RESOURCE_GROUP": "test-rg",
        "AZURE_CONTAINER_NAME": "test-container",
        "AZURE_LOCATION": "westeurope",
    }


def _make_ftp_plan() -> deploy_hooks.DeployPlan:
    """Plan with deploy_mode=full and extra_metadata populated."""
    plan = deploy_hooks.DeployPlan(
        name="test-container",
        location="westeurope",
        dns_label="test-container",
        deploy_mode="full",
        compose_service_name="web",
        deploy_role="app",
        app_image="ghcr.io/test/image:latest",
        caddy_image="caddy:2-alpine",
        other_image=None,
        app_cpu=1.0,
        app_memory=2.0,
        caddy_cpu=0.5,
        caddy_memory=0.5,
        other_cpu=0.25,
        other_memory=0.5,
        public_domain="test.example.com",
        app_port=8081,
    )
    plan.extra_metadata["_ftp_infra"] = {
        "rg": "test-rg",
        "location": "westeurope",
        "name": "test-container",
        "dns_label": "test-container",
        "storage_name": "testrgstorage",
        "identity_name": "test-rg-identity",
        "kv_name": "testrgkv",
        "data_share_name": "test-container-data",
    }
    return plan


def test_pre_render_yaml_captures_infra_metadata():
    """pre_render_yaml should populate plan.extra_metadata['_ftp_infra']."""
    hooks = deploy_customizations.get_hooks()
    env = {
        "AZURE_RESOURCE_GROUP": "my-rg",
        "AZURE_CONTAINER_NAME": "my-app",
        "AZURE_LOCATION": "northeurope",
    }
    ctx = deploy_hooks.DeployContext(
        repo_root=Path("."),
        env=env,
        args=argparse.Namespace(
            resource_group=None,
            container_name=None,
            dns_label=None,
            location=None,
            storage_name=None,
            identity_name=None,
            keyvault_name=None,
            data_share_name=None,
        ),
    )
    plan = _make_plan()
    hooks.pre_render_yaml(ctx, plan)

    infra = plan.extra_metadata.get("_ftp_infra")
    assert infra is not None
    assert infra["rg"] == "my-rg"
    assert infra["name"] == "my-app"
    assert infra["location"] == "northeurope"
    assert infra["dns_label"] == "my-app"
    assert infra["data_share_name"] == "my-app-data"
    assert infra["kv_name"] == "myrgkv"


def test_post_deploy_skips_ftp_when_not_full_mode():
    """post_deploy should be a no-op when deploy_mode is not 'full'."""
    hooks = deploy_customizations.get_hooks()
    env = _make_ftp_env()
    ctx = deploy_hooks.DeployContext(
        repo_root=Path("."),
        env=env,
        args=argparse.Namespace(service="web-caddy"),
    )

    plan = _make_ftp_plan()
    plan.deploy_mode = "web-caddy"

    # Should not raise or call any az commands
    with patch.object(deploy_customizations, "_run_az") as mock_az:
        hooks.post_deploy(ctx, plan, None)
        mock_az.assert_not_called()


def test_post_deploy_generates_ftp_yaml_with_correct_ports():
    """post_deploy should generate FTP YAML with correct ports and command."""
    hooks = deploy_customizations.get_hooks()
    env = _make_ftp_env()
    ctx = deploy_hooks.DeployContext(
        repo_root=Path("."),
        env=env,
        args=argparse.Namespace(service="full"),
    )

    plan = _make_ftp_plan()

    # Mock az commands and capture the YAML
    captured_yaml = {}

    def mock_run_az(args, *, capture_output=False, ignore_errors=False):
        if "storage" in args and "keys" in args:
            return "test-storage-key"
        if "identity" in args and "show" in args:
            return json.dumps({
                "id": "/subscriptions/s/resourcegroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id",
                "clientId": "test-client-id",
                "tenantId": "test-tenant-id",
            })
        if "container" in args and "create" in args:
            # Capture the YAML file path
            for i, a in enumerate(args):
                if a == "--file" and i + 1 < len(args):
                    with open(args[i + 1]) as f:
                        captured_yaml["text"] = f.read()
            return None
        if "container" in args and "show" in args:
            return None  # Container doesn't exist
        return None

    with patch.object(deploy_customizations, "_run_az", side_effect=mock_run_az):
        with patch.object(deploy_customizations, "_wait_for_container_deleted"):
            hooks.post_deploy(ctx, plan, None)

    assert "text" in captured_yaml, "FTP YAML was not generated"
    yaml_text = captured_yaml["text"]

    # Verify ports
    assert "port: 21" in yaml_text
    assert "port: 50000" in yaml_text
    assert "port: 50001" in yaml_text
    assert "port: 50002" in yaml_text
    assert "port: 50003" in yaml_text


def test_post_deploy_ftp_yaml_has_ftp_command():
    """FTP container should use azure_start.sh + python -m src.ftp_server."""
    hooks = deploy_customizations.get_hooks()
    env = _make_ftp_env()
    ctx = deploy_hooks.DeployContext(
        repo_root=Path("."),
        env=env,
        args=argparse.Namespace(service="full"),
    )

    plan = _make_ftp_plan()
    captured_yaml = {}

    def mock_run_az(args, *, capture_output=False, ignore_errors=False):
        if "storage" in args and "keys" in args:
            return "test-storage-key"
        if "identity" in args and "show" in args:
            return json.dumps({
                "id": "/subscriptions/s/rg/id",
                "clientId": "cid",
                "tenantId": "tid",
            })
        if "container" in args and "create" in args:
            for i, a in enumerate(args):
                if a == "--file" and i + 1 < len(args):
                    with open(args[i + 1]) as f:
                        captured_yaml["text"] = f.read()
            return None
        if "container" in args and "show" in args:
            return None
        return None

    with patch.object(deploy_customizations, "_run_az", side_effect=mock_run_az):
        with patch.object(deploy_customizations, "_wait_for_container_deleted"):
            hooks.post_deploy(ctx, plan, None)

    assert "text" in captured_yaml
    yaml_text = captured_yaml["text"]

    # ACI command should invoke azure_start.sh + ftp_server
    assert "/usr/local/bin/azure_start.sh" in yaml_text
    assert "src.ftp_server" in yaml_text


def test_post_deploy_creates_ftp_container_group():
    """post_deploy should call az container delete + create for FTP group."""
    hooks = deploy_customizations.get_hooks()
    env = _make_ftp_env()
    ctx = deploy_hooks.DeployContext(
        repo_root=Path("."),
        env=env,
        args=argparse.Namespace(service="full"),
    )

    plan = _make_ftp_plan()
    az_calls = []

    def mock_run_az(args, *, capture_output=False, ignore_errors=False):
        az_calls.append(args)
        if "storage" in args and "keys" in args:
            return "key"
        if "identity" in args and "show" in args:
            return json.dumps({"id": "/id", "clientId": "cid", "tenantId": "tid"})
        if "container" in args and "show" in args:
            return None  # container doesn't exist
        return None

    with patch.object(deploy_customizations, "_run_az", side_effect=mock_run_az):
        with patch.object(deploy_customizations, "_wait_for_container_deleted"):
            hooks.post_deploy(ctx, plan, None)

    # Should have called: storage keys list, identity show, container delete, container create
    az_commands = [" ".join(c[:3]) for c in az_calls]
    assert any("storage account keys" in cmd for cmd in az_commands), f"Missing storage keys call in {az_commands}"
    assert any("identity show" in cmd for cmd in az_commands), f"Missing identity show call in {az_commands}"
    assert any("container delete" in cmd for cmd in az_calls_to_str(az_calls)), f"Missing container delete"
    assert any("container create" in cmd for cmd in az_calls_to_str(az_calls)), f"Missing container create"


def az_calls_to_str(calls):
    return [" ".join(c) for c in calls]

