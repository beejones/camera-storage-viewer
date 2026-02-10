import argparse
from pathlib import Path

import pytest

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


def test_pre_validate_env_comments_out_unknown_deploy_keys(tmp_path: Path):
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
    assert "# FTP_CPU_CORES=0.25" in deploy_text
    assert "# FTP_MEMORY_GB=0.5" in deploy_text
