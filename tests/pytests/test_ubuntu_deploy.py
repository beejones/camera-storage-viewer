from pathlib import Path

from scripts.deploy.ubuntu_deploy import (
    build_compose_config_cmd,
    build_docker_build_cmd,
    build_docker_push_cmd,
    build_rsync_cmd,
    build_ssh_connectivity_cmd,
    build_ssh_cmd,
    parse_boolish,
    prepare_stack_content_for_portainer,
    rewrite_rendered_paths_for_remote,
    read_deploy_key,
    read_deploy_secret_key,
    read_dotenv_key,
)
from scripts.deploy.portainer_helpers import (
    _extract_webhook_token,
    _extract_container_names,
    build_portainer_webhook_urls_from_token,
    build_portainer_webhook_url,
    extract_ssh_hostname,
    portainer_ensure_running_remote_cmd,
)


def test_build_rsync_cmd_basic():
    cmd = build_rsync_cmd(
        sources=[Path("/repo/docker/docker-compose.yml"), Path("/repo/docker")],
        host="user@host",
        remote_dir=Path("/opt/protected-container"),
    )
    assert cmd[0] == "rsync"
    assert "user@host:/opt/protected-container/" in cmd


def test_build_compose_config_cmd_with_multiple_files():
    cmd = build_compose_config_cmd(compose_files=["docker/docker-compose.yml", "docker/docker-compose.ubuntu.yml"])
    assert cmd == [
        "docker",
        "compose",
        "-f",
        "docker/docker-compose.yml",
        "-f",
        "docker/docker-compose.ubuntu.yml",
        "config",
    ]


def test_build_docker_build_and_push_cmds():
    build_cmd = build_docker_build_cmd(
        app_image="ghcr.io/beejones/protected-container:latest",
        dockerfile="docker/Dockerfile",
        context_dir="docker",
    )
    push_cmd = build_docker_push_cmd(app_image="ghcr.io/beejones/protected-container:latest")
    assert build_cmd == [
        "docker",
        "build",
        "-f",
        "docker/Dockerfile",
        "-t",
        "ghcr.io/beejones/protected-container:latest",
        "docker",
    ]
    assert push_cmd == ["docker", "push", "ghcr.io/beejones/protected-container:latest"]


def test_build_ssh_cmd_basic():
    cmd = build_ssh_cmd(host="user@host", remote_command="echo hi")
    assert cmd == ["ssh", "user@host", "echo hi"]


def test_build_ssh_connectivity_cmd_basic():
    cmd = build_ssh_connectivity_cmd(host="user@host")
    assert cmd == ["ssh", "user@host", "echo SSH_OK"]


def test_portainer_ensure_running_remote_cmd_contains_expected_steps():
    out = portainer_ensure_running_remote_cmd(https_port=9943)
    assert "docker network inspect caddy" in out
    assert "docker ps --format '{{.Names}}'" in out
    assert "docker ps -a --format '{{.Names}}'" in out
    assert "docker start portainer" in out
    assert "docker run -d --name portainer" in out
    assert "docker network connect caddy portainer" in out
    assert "-p 9943:9443" in out


def test_extract_ssh_hostname_with_and_without_user():
    assert extract_ssh_hostname("ronny@192.168.1.45") == "192.168.1.45"
    assert extract_ssh_hostname("192.168.1.45") == "192.168.1.45"


def test_build_portainer_webhook_url_from_token():
    out = build_portainer_webhook_url(
        host="ronny@192.168.1.45",
        https_port=9943,
        webhook_token="abc123token",
    )
    assert out == "https://192.168.1.45:9943/api/stacks/webhooks/abc123token"


def test_build_portainer_webhook_urls_from_token_includes_fallback_endpoint():
    out = build_portainer_webhook_urls_from_token(
        host="ronny@192.168.1.45",
        https_port=9943,
        webhook_token="abc123token",
    )
    assert out == [
        "https://192.168.1.45:9943/api/stacks/webhooks/abc123token",
        "https://192.168.1.45:9943/api/webhooks/abc123token",
    ]


def test_read_deploy_secret_key_reads_token(tmp_path: Path):
    repo_root = tmp_path
    (repo_root / ".env.deploy.secrets").write_text("PORTAINER_WEBHOOK_TOKEN=from_file\n", encoding="utf-8")
    out = read_deploy_secret_key(repo_root=repo_root, key="PORTAINER_WEBHOOK_TOKEN")
    assert out == "from_file"


def test_read_deploy_secret_key_missing_file_returns_empty(tmp_path: Path):
    out = read_deploy_secret_key(repo_root=tmp_path, key="PORTAINER_WEBHOOK_TOKEN")
    assert out == ""


def test_read_deploy_key_reads_ubuntu_host(tmp_path: Path):
    repo_root = tmp_path
    (repo_root / ".env.deploy").write_text("UBUNTU_SSH_HOST=ronny@192.168.1.45\n", encoding="utf-8")
    out = read_deploy_key(repo_root=repo_root, key="UBUNTU_SSH_HOST")
    assert out == "ronny@192.168.1.45"


def test_read_deploy_key_reads_remote_dir(tmp_path: Path):
    repo_root = tmp_path
    (repo_root / ".env.deploy").write_text("UBUNTU_REMOTE_DIR=/home/ronny/containers/protected-container\n", encoding="utf-8")
    out = read_deploy_key(repo_root=repo_root, key="UBUNTU_REMOTE_DIR")
    assert out == "/home/ronny/containers/protected-container"


def test_read_dotenv_key_missing_file_returns_empty(tmp_path: Path):
    out = read_dotenv_key(dotenv_path=tmp_path / "missing.env", key="ANY_KEY")
    assert out == ""


def test_parse_boolish_truthy_falsey_and_default():
    assert parse_boolish("true") is True
    assert parse_boolish("yes") is True
    assert parse_boolish("1") is True
    assert parse_boolish("false") is False
    assert parse_boolish("0") is False
    assert parse_boolish("", default=True) is True
    assert parse_boolish("not-a-bool", default=False) is False


def test_extract_webhook_token_from_common_shapes():
    assert _extract_webhook_token({"Webhook": "abc"}) == "abc"
    assert _extract_webhook_token({"token": "def"}) == "def"
    assert _extract_webhook_token({"data": {"webhookToken": "ghi"}}) == "ghi"
    assert _extract_webhook_token([{"x": 1}, {"WebhookToken": "jkl"}]) == "jkl"


def test_prepare_stack_content_for_portainer_replaces_app_build_with_image():
    stack_content = """
services:
    app:
        build:
            context: /tmp/repo/docker
        environment:
            A: B
    caddy:
        image: caddy:2-alpine
"""
    out = prepare_stack_content_for_portainer(
        stack_content=stack_content,
        app_image="ghcr.io/beejones/protected-container:latest",
    )
    assert "build:" not in out
    assert "image: ghcr.io/beejones/protected-container:latest" in out


def test_prepare_stack_content_for_portainer_errors_on_remaining_build_service():
    stack_content = """
services:
    worker:
        build:
            context: /tmp/repo/worker
"""
    try:
        prepare_stack_content_for_portainer(stack_content=stack_content, app_image="")
        assert False, "expected SystemExit"
    except SystemExit as exc:
        assert "services still use build contexts" in str(exc)


def test_extract_container_names_reads_service_container_names():
    stack_content = """
services:
    app:
        container_name: protected-container
    caddy:
        container_name: tls-proxy
    worker:
        image: busybox
"""
    out = _extract_container_names(stack_content)
    assert out == ["protected-container", "tls-proxy"]


def test_rewrite_rendered_paths_for_remote_replaces_local_repo_root():
    stack_content = "volumes:\n  - /home/ronny/dev/protected-azure-container/.env:/home/coder/.env:ro\n"
    out = rewrite_rendered_paths_for_remote(
        stack_content=stack_content,
        local_repo_root=Path("/home/ronny/dev/protected-azure-container"),
        remote_dir=Path("/home/ronny/containers/protected-container"),
    )
    assert "/home/ronny/dev/protected-azure-container" not in out
    assert "/home/ronny/containers/protected-container/.env" in out
