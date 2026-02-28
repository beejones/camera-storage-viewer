#!/usr/bin/env python3
"""Deploy this repo to an Ubuntu server over SSH.

This is the Ubuntu-target parallel to `azure_deploy_container.py`.

It does NOT require Azure CLI or Key Vault. It syncs deployment files to the
remote host, ensures Portainer is running, and triggers a Portainer stack
webhook for deployment.

Security note: this script shells out to `ssh` and `rsync`.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values
import sys
import urllib3
import yaml

sys.path.append(str(Path(__file__).parent))

import caddy_register
import portainer_helpers


ENV_PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
ENV_WEB_PORT = "WEB_PORT"
ENV_APP_IMAGE = "APP_IMAGE"
ENV_DOCKERFILE = "DOCKERFILE"
ENV_GHCR_USERNAME = "GHCR_USERNAME"
ENV_GHCR_TOKEN = "GHCR_TOKEN"
ENV_UBUNTU_SSH_HOST = "UBUNTU_SSH_HOST"
ENV_UBUNTU_REMOTE_DIR = "UBUNTU_REMOTE_DIR"
ENV_UBUNTU_COMPOSE_FILES = "UBUNTU_COMPOSE_FILES"
ENV_UBUNTU_BUILD_PUSH = "UBUNTU_BUILD_PUSH"
ENV_UBUNTU_SYNC_SECRETS = "UBUNTU_SYNC_SECRETS"
ENV_PORTAINER_HTTPS_PORT = "PORTAINER_HTTPS_PORT"
ENV_PORTAINER_WEBHOOK_INSECURE = "PORTAINER_WEBHOOK_INSECURE"
ENV_PORTAINER_WEBHOOK_URL = "PORTAINER_WEBHOOK_URL"
ENV_PORTAINER_WEBHOOK_TOKEN = "PORTAINER_WEBHOOK_TOKEN"
ENV_PORTAINER_ACCESS_TOKEN = "PORTAINER_ACCESS_TOKEN"
ENV_PORTAINER_STACK_NAME = "PORTAINER_STACK_NAME"
ENV_PORTAINER_ENDPOINT_ID = "PORTAINER_ENDPOINT_ID"
ENV_CADDY_PROXY_DIR = "CADDY_PROXY_DIR"
ENV_STORAGE_MANAGER_API_URL = "STORAGE_MANAGER_API_URL"


_STORAGE_MANAGER_LABEL_PATTERN = re.compile(r"^storage-manager\.(\d+)\.(.+)$")


def _subprocess_error_text(exc: subprocess.CalledProcessError) -> str:
    stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
    stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
    text = (stderr.strip() or stdout.strip() or "").strip()
    return text


def _ssh_failure_hint(error_text: str) -> str:
    lowered = error_text.lower()
    if "no route to host" in lowered:
        return "No route to host. Check VPN/LAN reachability and UBUNTU_SSH_HOST."
    if "connection timed out" in lowered:
        return "SSH timed out. Verify the server is online and port 22 is reachable."
    if "connection refused" in lowered:
        return "SSH connection refused. Confirm SSH daemon is running and port 22 is open."
    if "permission denied" in lowered:
        return "SSH authentication failed. Verify SSH key access for the configured user."
    if "could not resolve hostname" in lowered:
        return "Host resolution failed. Check UBUNTU_SSH_HOST for typos/DNS issues."
    return ""


def _run(cmd: list[str], *, check: bool = True, action: str | None = None) -> None:
    try:
        subprocess.run(cmd, check=check)
    except subprocess.CalledProcessError as exc:
        action_text = action or f"Command failed: {' '.join(cmd)}"
        detail = _subprocess_error_text(exc)
        message = f"{action_text} (exit code {exc.returncode})."
        if detail:
            message = f"{message} {detail}"
        if cmd and cmd[0] == "ssh":
            hint = _ssh_failure_hint(detail)
            if hint:
                message = f"{message} {hint}"
        raise SystemExit(message)


def build_rsync_cmd(*, sources: list[Path], host: str, remote_dir: Path) -> list[str]:
    srcs = [str(p) for p in sources]
    # Trailing slash on remote_dir ensures rsync copies into the dir.
    dest = f"{host}:{str(remote_dir)}/"
    return ["rsync", "-az", "--mkpath", *srcs, dest]


def build_ssh_cmd(*, host: str, remote_command: str) -> list[str]:
    return ["ssh", host, remote_command]


def build_ssh_connectivity_cmd(*, host: str) -> list[str]:
    return build_ssh_cmd(host=host, remote_command="echo SSH_OK")


def discover_remote_caddyfile_path(*, host: str) -> str:
    cmd = (
        "docker inspect central-proxy "
        "--format '{{range .Mounts}}{{if eq .Destination \"/etc/caddy/Caddyfile\"}}{{.Source}}{{end}}{{end}}'"
    )
    result = subprocess.run(
        build_ssh_cmd(host=host, remote_command=cmd),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    source_path = str(result.stdout or "").strip()
    return source_path


def build_docker_build_cmd(*, app_image: str, dockerfile: str, context_dir: str) -> list[str]:
    return ["docker", "build", "-f", dockerfile, "-t", app_image, context_dir]


def build_docker_push_cmd(*, app_image: str) -> list[str]:
    return ["docker", "push", app_image]


def build_compose_config_cmd(*, compose_files: list[str]) -> list[str]:
    cmd = ["docker", "compose"]
    for compose_file in compose_files:
        cmd.extend(["-f", compose_file])
    cmd.append("config")
    return cmd


def render_compose_stack_content(*, repo_root: Path, compose_files: list[str]) -> str:
    cmd = build_compose_config_cmd(compose_files=compose_files)
    compose_env = dict(os.environ)
    for dotenv_name in [".env", ".env.secrets", ".env.deploy", ".env.deploy.secrets"]:
        dotenv_path = repo_root / dotenv_name
        if not dotenv_path.exists():
            continue
        raw = dotenv_values(dotenv_path)
        for key, value in raw.items():
            if not key:
                continue
            compose_env[str(key)] = "" if value is None else str(value)

    result = subprocess.run(
        cmd,
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
        env=compose_env,
    )
    if result.returncode != 0:
        err = str(result.stderr or "").strip() or str(result.stdout or "").strip()
        raise SystemExit(f"Failed to render compose config for Portainer stack creation: {err}")
    content = str(result.stdout or "").strip()
    if not content:
        raise SystemExit("Failed to render compose config for Portainer stack creation: empty output")
    return content


def rewrite_rendered_paths_for_remote(*, stack_content: str, local_repo_root: Path, remote_dir: Path) -> str:
    local_root = str(local_repo_root.resolve())
    remote_root = str(remote_dir)
    return stack_content.replace(local_root, remote_root)


def prepare_stack_content_for_portainer(*, stack_content: str, app_image: str) -> str:
    payload = yaml.safe_load(stack_content)
    if not isinstance(payload, dict):
        raise SystemExit("Rendered compose config is not a valid mapping")

    services = payload.get("services")
    if not isinstance(services, dict):
        return stack_content

    # Ubuntu/Portainer uses the centralized proxy stack for :80/:443.
    # Drop local caddy sidecar service from the app stack to avoid bind conflicts.
    services.pop("caddy", None)

    web_service = services.get("web")
    if isinstance(web_service, dict):
        # Centralized proxy reaches web over the external `caddy` network.
        # Keep web internal networking, but remove host port binding for Ubuntu deploys.
        web_service.pop("ports", None)

        existing_networks = web_service.get("networks")
        merged_networks: list[str] = []
        if isinstance(existing_networks, list):
            merged_networks = [str(n) for n in existing_networks if str(n).strip()]
        elif isinstance(existing_networks, str):
            merged_networks = [existing_networks]

        if "caddy" not in merged_networks:
            merged_networks.append("caddy")
        web_service["networks"] = merged_networks

    top_networks = payload.get("networks")
    if not isinstance(top_networks, dict):
        top_networks = {}
        payload["networks"] = top_networks
    top_networks.setdefault("caddy", {"external": True, "name": "caddy"})

    remaining_build_services: list[str] = []
    app_image_value = app_image.strip()

    for service_name, service_payload in services.items():
        if not isinstance(service_payload, dict):
            continue
        if "build" not in service_payload:
            continue

        if app_image_value:
            service_payload.pop("build", None)
            service_payload["image"] = app_image_value
            continue

        remaining_build_services.append(str(service_name))

    if remaining_build_services:
        raise SystemExit(
            "Portainer stack creation requires image-based services. "
            f"These services still use build contexts: {remaining_build_services}. "
            "Set APP_IMAGE and/or adjust compose files for image-based deployment."
        )

    return yaml.safe_dump(payload, sort_keys=False)




def portainer_ensure_running_remote_cmd(*, https_port: int) -> str:
    return (
        "if docker ps --format '{{.Names}}' | grep -Fxq portainer; then "
        "echo '[ubuntu-deploy] Portainer already running'; "
        "elif docker ps -a --format '{{.Names}}' | grep -Fxq portainer; then "
        "echo '[ubuntu-deploy] Starting existing Portainer container'; "
        "docker start portainer >/dev/null; "
        "else "
        "echo '[ubuntu-deploy] Creating Portainer container'; "
        "docker volume create portainer_data >/dev/null && "
        f"docker run -d --name portainer --restart=unless-stopped -p 8000:8000 -p {https_port}:9443 "
        "-v /var/run/docker.sock:/var/run/docker.sock -v portainer_data:/data "
        "portainer/portainer-ce:latest >/dev/null; "
        "fi"
    )


def ghcr_login_pull_remote_cmd(*, image: str, username: str, token: str) -> str:
    return (
        f"printf %s {shlex.quote(token)} | docker login ghcr.io -u {shlex.quote(username)} --password-stdin >/dev/null "
        f"&& docker pull {shlex.quote(image)} >/dev/null"
    )


def docker_login_local(*, registry: str, username: str, token: str) -> None:
    subprocess.run(
        ["docker", "login", registry, "-u", username, "--password-stdin"],
        input=token,
        text=True,
        check=True,
    )


def build_and_push_local_image(*, repo_root: Path, app_image: str, dockerfile: str) -> None:
    dockerfile_path = repo_root / dockerfile
    if not dockerfile_path.exists():
        raise SystemExit(f"Dockerfile not found for build/push: {dockerfile_path}")
    # Dockerfile COPY paths in this repo are written relative to repository root.
    # Always build with repo root context so `COPY requirements.*`, `COPY src`, etc. resolve.
    context_dir = "."
    build_cmd = build_docker_build_cmd(app_image=app_image, dockerfile=dockerfile, context_dir=context_dir)
    push_cmd = build_docker_push_cmd(app_image=app_image)
    subprocess.run(build_cmd, cwd=str(repo_root), check=True)
    subprocess.run(push_cmd, cwd=str(repo_root), check=True)




def read_dotenv_key(*, dotenv_path: Path, key: str) -> str:
    if not dotenv_path.exists():
        return ""
    raw = dotenv_values(dotenv_path)
    return str(raw.get(key) or "").strip()


def read_deploy_key(*, repo_root: Path, key: str) -> str:
    return read_dotenv_key(dotenv_path=repo_root / ".env.deploy", key=key)


def read_deploy_secret_key(*, repo_root: Path, key: str) -> str:
    return read_dotenv_key(dotenv_path=repo_root / ".env.deploy.secrets", key=key)


def parse_boolish(value: str, *, default: bool = False) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_compose_labels(labels: Any) -> dict[str, str]:
    if isinstance(labels, dict):
        return {str(k): str(v) for k, v in labels.items()}

    out: dict[str, str] = {}
    if isinstance(labels, list):
        for item in labels:
            text = str(item or "")
            if "=" not in text:
                continue
            key, value = text.split("=", 1)
            out[str(key).strip()] = str(value).strip()
    return out


def _coerce_label_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    parsed = yaml.safe_load(value)
    if isinstance(parsed, (str, int, float, bool)) or parsed is None:
        return parsed
    return value


def collect_storage_manager_registrations(*, stack_content: str) -> list[dict[str, Any]]:
    payload = yaml.safe_load(stack_content)
    if not isinstance(payload, dict):
        return []

    services = payload.get("services")
    if not isinstance(services, dict):
        return []

    registrations_by_service: list[tuple[str, int, dict[str, Any]]] = []

    for service_name, service_payload in services.items():
        if not isinstance(service_payload, dict):
            continue

        normalized_labels = _normalize_compose_labels(service_payload.get("labels"))
        buckets: dict[int, dict[str, Any]] = {}

        for label_key, label_value in normalized_labels.items():
            match = _STORAGE_MANAGER_LABEL_PATTERN.match(label_key)
            if not match:
                continue

            index = int(match.group(1))
            key = str(match.group(2)).strip()
            if not key:
                continue

            if index not in buckets:
                buckets[index] = {}

            buckets[index][key] = _coerce_label_value(label_value)

        for index, values in buckets.items():
            required = ["volume", "path", "algorithm"]
            missing = [field for field in required if not values.get(field)]
            if missing:
                missing_fields = ", ".join(missing)
                raise SystemExit(
                    "Invalid storage-manager label registration in service "
                    f"'{service_name}' index {index}: missing required fields: {missing_fields}"
                )

            params = {
                key: value
                for key, value in values.items()
                if key not in {"volume", "path", "algorithm", "description"}
            }

            registration: dict[str, Any] = {
                "volume_name": str(values["volume"]),
                "path": str(values["path"]),
                "algorithm": str(values["algorithm"]),
                "params": params,
                "source_service": str(service_name),
                "source_index": index,
            }
            if values.get("description") is not None:
                registration["description"] = str(values["description"])

            registrations_by_service.append((str(service_name), index, registration))

    registrations_by_service.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in registrations_by_service]


def _storage_manager_register_url(api_url: str) -> str:
    base = str(api_url or "").strip().rstrip("/")
    if base.endswith("/api/register"):
        return base
    return f"{base}/api/register"


def register_storage_manager_registrations(
    *,
    api_url: str,
    registrations: list[dict[str, Any]],
    timeout_seconds: int = 10,
) -> None:
    endpoint = _storage_manager_register_url(api_url)

    for item in registrations:
        payload: dict[str, Any] = {
            "volume_name": item["volume_name"],
            "path": item["path"],
            "algorithm": item["algorithm"],
            "params": item.get("params") or {},
        }
        if item.get("description"):
            payload["description"] = item["description"]

        response = requests.post(endpoint, json=payload, timeout=timeout_seconds)
        if response.status_code < 200 or response.status_code >= 300:
            detail = str(response.text or "").strip()
            source_service = str(item.get("source_service") or "unknown")
            source_index = str(item.get("source_index") or "?")
            raise SystemExit(
                f"Storage registration failed for {source_service}[{source_index}] via {endpoint}: "
                f"HTTP {response.status_code} {detail}"
            )


def main(argv: list[str] | None = None, repo_root_override: Path | None = None) -> None:
    parser = argparse.ArgumentParser(description="Deploy to Ubuntu server via Portainer stack webhook")
    parser.add_argument(
        "--host",
        default=None,
        help="SSH target in the form user@host. Resolution: CLI -> UBUNTU_SSH_HOST env var -> .env.deploy",
    )
    parser.add_argument(
        "--remote-dir",
        default=None,
        help=(
            "Remote directory to sync files into. "
            "Resolution: CLI --remote-dir -> UBUNTU_REMOTE_DIR env var -> .env.deploy -> /opt/camera-storage-viewer"
        ),
    )
    parser.add_argument(
        "--compose-files",
        default=None,
        help=(
            "Comma-separated compose files relative to repo root. "
            "Resolution: CLI -> UBUNTU_COMPOSE_FILES env var -> .env.deploy -> built-in default"
        ),
    )
    parser.add_argument(
        "--sync-secrets",
        action="store_true",
        help="Sync .env and .env.secrets to the remote dir before triggering Portainer stack deploy (overrides UBUNTU_SYNC_SECRETS)",
    )
    parser.add_argument(
        "--portainer-webhook-url",
        default=None,
        help=(
            "Portainer stack webhook URL. "
            "Resolution: CLI -> PORTAINER_WEBHOOK_URL env var -> .env.deploy.secrets/.env.deploy"
        ),
    )
    parser.add_argument(
        "--portainer-webhook-token",
        default=None,
        help=(
            "Portainer stack webhook token (last URL segment). "
            "Resolution order: CLI flag -> PORTAINER_WEBHOOK_TOKEN env var -> .env.deploy.secrets"
        ),
    )
    parser.add_argument(
        "--portainer-https-port",
        type=int,
        default=None,
        help="Host HTTPS port for Portainer when auto-creating container (resolution: CLI/env/.env.deploy -> 9943)",
    )
    parser.add_argument(
        "--portainer-webhook-insecure",
        action="store_true",
        help="Pass -k to curl when triggering Portainer webhook",
    )
    parser.add_argument(
        "--skip-build-push",
        action="store_true",
        help="Skip default local docker build+push before deployment",
    )
    parser.add_argument(
        "--storage-manager-api-url",
        default=None,
        help=(
            "Storage Manager API base URL (e.g. https://storage.example.com or https://storage.example.com/api/register). "
            "Resolution: CLI -> STORAGE_MANAGER_API_URL env var -> .env.deploy"
        ),
    )

    args = parser.parse_args(argv)

    step_number = 0
    step_color = "\033[95m"
    color_reset = "\033[0m"

    def log_step(message: str, *, icon: str = "🚀") -> None:
        nonlocal step_number
        step_number += 1
        print(f"{step_color}[ubuntu-deploy] {icon} Step {step_number}: {message}{color_reset}")

    def log_info(message: str, *, icon: str = "ℹ️") -> None:
        print(f"[ubuntu-deploy] {icon} {message}")

    repo_root = repo_root_override or Path(__file__).resolve().parents[2]

    resolved_host = str(args.host or "").strip()
    if not resolved_host:
        resolved_host = str(os.getenv(ENV_UBUNTU_SSH_HOST) or "").strip()
    if not resolved_host:
        resolved_host = read_deploy_key(repo_root=repo_root, key=ENV_UBUNTU_SSH_HOST)
    if not resolved_host:
        raise SystemExit("Missing SSH host: provide --host, set UBUNTU_SSH_HOST, or add UBUNTU_SSH_HOST to .env.deploy")

    resolved_remote_dir = str(args.remote_dir or "").strip()
    if not resolved_remote_dir:
        resolved_remote_dir = str(os.getenv(ENV_UBUNTU_REMOTE_DIR) or "").strip()
    if not resolved_remote_dir:
        resolved_remote_dir = read_deploy_key(repo_root=repo_root, key=ENV_UBUNTU_REMOTE_DIR)
    if not resolved_remote_dir:
        resolved_remote_dir = "/opt/camera-storage-viewer"
    remote_dir = Path(resolved_remote_dir)

    resolved_compose_files_raw = str(args.compose_files or "").strip()
    if not resolved_compose_files_raw:
        resolved_compose_files_raw = str(os.getenv(ENV_UBUNTU_COMPOSE_FILES) or "").strip()
    if not resolved_compose_files_raw:
        resolved_compose_files_raw = read_deploy_key(repo_root=repo_root, key=ENV_UBUNTU_COMPOSE_FILES)

    resolved_app_image = str(os.getenv(ENV_APP_IMAGE) or "").strip()
    if not resolved_app_image:
        resolved_app_image = read_deploy_key(repo_root=repo_root, key=ENV_APP_IMAGE)

    resolved_dockerfile = str(os.getenv(ENV_DOCKERFILE) or "").strip()
    if not resolved_dockerfile:
        resolved_dockerfile = read_deploy_key(repo_root=repo_root, key=ENV_DOCKERFILE)
    if not resolved_dockerfile:
        resolved_dockerfile = "docker/Dockerfile"

    resolved_build_push_enabled = not bool(args.skip_build_push)
    if resolved_build_push_enabled:
        build_push_raw = str(os.getenv(ENV_UBUNTU_BUILD_PUSH) or "").strip()
        if not build_push_raw:
            build_push_raw = read_deploy_key(repo_root=repo_root, key=ENV_UBUNTU_BUILD_PUSH)
        resolved_build_push_enabled = parse_boolish(build_push_raw, default=True)

    resolved_portainer_https_port_raw = str(args.portainer_https_port or "").strip()
    if not resolved_portainer_https_port_raw:
        resolved_portainer_https_port_raw = str(os.getenv(ENV_PORTAINER_HTTPS_PORT) or "").strip()
    if not resolved_portainer_https_port_raw:
        resolved_portainer_https_port_raw = read_deploy_key(repo_root=repo_root, key=ENV_PORTAINER_HTTPS_PORT)
    resolved_portainer_https_port = int(resolved_portainer_https_port_raw or "9943")

    resolved_sync_secrets = bool(args.sync_secrets)
    if not resolved_sync_secrets:
        sync_secrets_raw = str(os.getenv(ENV_UBUNTU_SYNC_SECRETS) or "").strip()
        if not sync_secrets_raw:
            sync_secrets_raw = read_deploy_key(repo_root=repo_root, key=ENV_UBUNTU_SYNC_SECRETS)
        resolved_sync_secrets = parse_boolish(sync_secrets_raw, default=True)

    resolved_portainer_webhook_insecure = bool(args.portainer_webhook_insecure)
    if not resolved_portainer_webhook_insecure:
        webhook_insecure_raw = str(os.getenv(ENV_PORTAINER_WEBHOOK_INSECURE) or "").strip()
        if not webhook_insecure_raw:
            webhook_insecure_raw = read_deploy_key(repo_root=repo_root, key=ENV_PORTAINER_WEBHOOK_INSECURE)
        resolved_portainer_webhook_insecure = parse_boolish(webhook_insecure_raw, default=False)

    if resolved_portainer_webhook_insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    resolved_portainer_webhook_url = str(args.portainer_webhook_url or "").strip()
    if not resolved_portainer_webhook_url:
        resolved_portainer_webhook_url = str(os.getenv(ENV_PORTAINER_WEBHOOK_URL) or "").strip()
    if not resolved_portainer_webhook_url:
        resolved_portainer_webhook_url = read_deploy_secret_key(repo_root=repo_root, key=ENV_PORTAINER_WEBHOOK_URL)
    if not resolved_portainer_webhook_url:
        resolved_portainer_webhook_url = read_deploy_key(repo_root=repo_root, key=ENV_PORTAINER_WEBHOOK_URL)

    resolved_portainer_token = str(args.portainer_webhook_token or "").strip()
    if not resolved_portainer_token:
        resolved_portainer_token = str(os.getenv(ENV_PORTAINER_WEBHOOK_TOKEN) or "").strip()
    if not resolved_portainer_token:
        resolved_portainer_token = read_deploy_secret_key(repo_root=repo_root, key=ENV_PORTAINER_WEBHOOK_TOKEN)

    resolved_portainer_access_token = str(os.getenv(ENV_PORTAINER_ACCESS_TOKEN) or "").strip()
    if not resolved_portainer_access_token:
        resolved_portainer_access_token = read_deploy_secret_key(repo_root=repo_root, key=ENV_PORTAINER_ACCESS_TOKEN)

    resolved_portainer_stack_name = str(os.getenv(ENV_PORTAINER_STACK_NAME) or "").strip()
    if not resolved_portainer_stack_name:
        resolved_portainer_stack_name = read_deploy_key(repo_root=repo_root, key=ENV_PORTAINER_STACK_NAME)
    if not resolved_portainer_stack_name:
        resolved_portainer_stack_name = "camera-storage-viewer"

    resolved_portainer_endpoint_id = str(os.getenv(ENV_PORTAINER_ENDPOINT_ID) or "").strip()
    if not resolved_portainer_endpoint_id:
        resolved_portainer_endpoint_id = read_deploy_key(repo_root=repo_root, key=ENV_PORTAINER_ENDPOINT_ID)

    resolved_storage_manager_api_url = str(args.storage_manager_api_url or "").strip()
    if not resolved_storage_manager_api_url:
        resolved_storage_manager_api_url = str(os.getenv(ENV_STORAGE_MANAGER_API_URL) or "").strip()
    if not resolved_storage_manager_api_url:
        resolved_storage_manager_api_url = read_deploy_key(repo_root=repo_root, key=ENV_STORAGE_MANAGER_API_URL)

    resolved_ghcr_username = str(os.getenv(ENV_GHCR_USERNAME) or "").strip()
    if not resolved_ghcr_username:
        resolved_ghcr_username = read_deploy_key(repo_root=repo_root, key=ENV_GHCR_USERNAME)

    resolved_ghcr_token = str(os.getenv(ENV_GHCR_TOKEN) or "").strip()
    if not resolved_ghcr_token:
        resolved_ghcr_token = read_deploy_secret_key(repo_root=repo_root, key=ENV_GHCR_TOKEN)

    if resolved_build_push_enabled:
        if not resolved_app_image:
            raise SystemExit("APP_IMAGE is required when build/push is enabled")
        if resolved_app_image.startswith("ghcr.io/"):
            if not resolved_ghcr_username or not resolved_ghcr_token:
                raise SystemExit(
                    "GHCR_USERNAME and GHCR_TOKEN are required to build/push APP_IMAGE to ghcr.io. "
                    "Set them in .env.deploy/.env.deploy.secrets or use --skip-build-push."
                )
            log_step("Logging into GHCR for local build/push", icon="🔐")
            docker_login_local(registry="ghcr.io", username=resolved_ghcr_username, token=resolved_ghcr_token)

        log_step("Building and pushing APP_IMAGE locally", icon="🏗️")
        build_and_push_local_image(
            repo_root=repo_root,
            app_image=resolved_app_image,
            dockerfile=resolved_dockerfile,
        )

    has_portainer_api_auth = bool(resolved_portainer_access_token)

    if not (resolved_portainer_webhook_url or resolved_portainer_token or has_portainer_api_auth):
        raise SystemExit(
            "Provide Portainer webhook URL/token or API auth (PORTAINER_ACCESS_TOKEN)"
        )
    if resolved_portainer_https_port < 1 or resolved_portainer_https_port > 65535:
        raise SystemExit("--portainer-https-port must be in range 1-65535")

    compose_files = (
        [s.strip() for s in resolved_compose_files_raw.split(",") if s.strip()]
        if resolved_compose_files_raw
        else ["docker/docker-compose.yml"]
    )

    # Validate required local files exist.
    required_paths: list[Path] = [repo_root / cf for cf in compose_files] + [repo_root / "docker"]
    missing = [str(p) for p in required_paths if not p.exists()]
    if missing:
        raise SystemExit(f"Missing required files: {missing}")

    stack_file_content_raw = render_compose_stack_content(repo_root=repo_root, compose_files=compose_files)
    stack_file_content_remote = rewrite_rendered_paths_for_remote(
        stack_content=stack_file_content_raw,
        local_repo_root=repo_root,
        remote_dir=remote_dir,
    )
    stack_file_content = prepare_stack_content_for_portainer(
        stack_content=stack_file_content_remote,
        app_image=resolved_app_image,
    )
    storage_registrations = collect_storage_manager_registrations(stack_content=stack_file_content)
    stack_includes_storage_manager = "storage-manager" in str(stack_file_content)

    registration_service_name = resolved_portainer_stack_name or remote_dir.name
    try:
        rendered_payload = yaml.safe_load(stack_file_content)
        rendered_services = rendered_payload.get("services") if isinstance(rendered_payload, dict) else None
        if isinstance(rendered_services, dict):
            web_payload = rendered_services.get("web")
            if isinstance(web_payload, dict):
                web_container_name = str(web_payload.get("container_name") or "").strip()
                if web_container_name:
                    registration_service_name = web_container_name
    except Exception:
        pass

    log_step("Prepared deployment plan", icon="🧭")
    log_info(f"Target: {resolved_host}")
    log_info(f"Remote dir: {remote_dir}")
    log_info(f"Compose files: {compose_files}")
    if storage_registrations:
        log_info(f"Detected {len(storage_registrations)} storage-manager label registration(s)", icon="🧹")

    log_step("Checking SSH connectivity", icon="🔌")
    try:
        subprocess.run(
            build_ssh_connectivity_cmd(host=resolved_host),
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(
            "SSH connectivity check timed out after 15s. "
            "Verify the server is online and reachable on port 22."
        )
    except subprocess.CalledProcessError as exc:
        detail = _subprocess_error_text(exc)
        message = "SSH connectivity check failed before deployment."
        if detail:
            message = f"{message} {detail}"
        hint = _ssh_failure_hint(detail)
        if hint:
            message = f"{message} {hint}"
        raise SystemExit(message)

    # Ensure remote dir exists.
    log_step("Ensuring remote deployment directory exists", icon="📁")
    _run(
        build_ssh_cmd(host=resolved_host, remote_command=f"mkdir -p {shlex.quote(str(remote_dir))}"),
        action="Failed to create remote deployment directory over SSH",
    )

    # Sync compose files and docker/ directory.
    log_step("Syncing compose files and docker assets", icon="📦")
    sync_paths: list[Path] = [repo_root / cf for cf in compose_files] + [repo_root / "docker"]
    _run(
        build_rsync_cmd(sources=sync_paths, host=resolved_host, remote_dir=remote_dir),
        action="Failed to sync compose files and docker assets",
    )

    if resolved_sync_secrets:
        env_paths: list[Path] = []
        for name in [".env", ".env.secrets", ".env.deploy", ".env.deploy.secrets"]:
            p = repo_root / name
            if p.exists():
                env_paths.append(p)
            else:
                log_info(f"Skipping missing {p}", icon="⚠️")
        if env_paths:
            log_step("Syncing environment files", icon="🔐")
            _run(
                build_rsync_cmd(sources=env_paths, host=resolved_host, remote_dir=remote_dir),
                action="Failed to sync environment files",
            )
        else:
            log_info("No env files to sync")

    log_step("Ensuring Portainer is running on remote host", icon="🛡️")
    _run(
        build_ssh_cmd(
            host=resolved_host,
            remote_command=portainer_helpers.portainer_ensure_running_remote_cmd(https_port=resolved_portainer_https_port),
        ),
        action="Failed to ensure Portainer is running on the remote host",
    )

    if resolved_app_image and resolved_app_image.startswith("ghcr.io/") and resolved_ghcr_username and resolved_ghcr_token:
        log_step("Logging into GHCR and pre-pulling APP_IMAGE on remote host", icon="🔐")
        _run(
            build_ssh_cmd(
                host=resolved_host,
                remote_command=ghcr_login_pull_remote_cmd(
                    image=resolved_app_image,
                    username=resolved_ghcr_username,
                    token=resolved_ghcr_token,
                ),
            ),
            action="Failed remote GHCR login/pull for APP_IMAGE",
        )

    log_step("Ensuring Central Caddy Proxy is running", icon="🌐")
    # Check if the global proxy container exists
    caddy_check_cmd = build_ssh_cmd(
        host=resolved_host,
        remote_command="docker ps -a --format '{{.Names}}' | grep -Fxq central-proxy"
    )
    result = subprocess.run(caddy_check_cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        log_info("Central proxy missing. Deploying via ubuntu_deploy_proxy.sh...")
        proxy_script = repo_root / "scripts" / "deploy" / "ubuntu_deploy_proxy.sh"
        if proxy_script.exists():
            try:
                subprocess.run(["bash", str(proxy_script)], check=True)
            except subprocess.CalledProcessError as exc:
                detail = _subprocess_error_text(exc)
                message = "Failed to deploy central proxy via ubuntu_deploy_proxy.sh"
                if detail:
                    message = f"{message}: {detail}"
                raise SystemExit(message)
        else:
            log_info(f"Warning: {proxy_script} not found. Cannot auto-deploy proxy.", icon="⚠️")
    else:
        log_info("Central proxy is already running.")

    if has_portainer_api_auth:
        log_step("Testing Portainer access token", icon="🔑")
        if not portainer_helpers.is_portainer_access_token_valid(
            host=resolved_host,
            https_port=resolved_portainer_https_port,
            insecure=resolved_portainer_webhook_insecure,
            access_token=resolved_portainer_access_token,
        ):
            raise SystemExit(
                "Portainer access token is invalid or expired. "
                "Update PORTAINER_ACCESS_TOKEN in .env.deploy.secrets and retry. "
            )

    if has_portainer_api_auth:
        log_step("Deploying stack through Portainer API", icon="🌐")
        try:
            resolved_portainer_webhook_url = portainer_helpers.resolve_portainer_webhook_url_via_api(
                host=resolved_host,
                https_port=resolved_portainer_https_port,
                insecure=resolved_portainer_webhook_insecure,
                stack_name=resolved_portainer_stack_name,
                endpoint_id=resolved_portainer_endpoint_id,
                access_token=resolved_portainer_access_token,
                stack_file_content=stack_file_content,
                ssh_run_fn=lambda remote_command: _run(build_ssh_cmd(host=resolved_host, remote_command=remote_command)),
            )
        except requests.HTTPError as exc:
            status_code = int(getattr(getattr(exc, "response", None), "status_code", 0) or 0)
            if status_code == 401:
                raise SystemExit(
                    "Portainer access token was rejected (401 Unauthorized). "
                    "Update PORTAINER_ACCESS_TOKEN in .env.deploy.secrets and retry. "
                )
            else:
                raise

    if has_portainer_api_auth and not resolved_portainer_webhook_url:
        log_step("Stack deployed via Portainer API; webhook token not returned, skipping webhook trigger", icon="✅")
    else:
        webhook_urls = (
            [resolved_portainer_webhook_url]
            if resolved_portainer_webhook_url
            else portainer_helpers.build_portainer_webhook_urls_from_token(
                host=resolved_host,
                https_port=resolved_portainer_https_port,
                webhook_token=resolved_portainer_token,
            )
        )

        log_step("Triggering Portainer webhook", icon="🪝")
        portainer_helpers.trigger_portainer_webhook(
            urls=webhook_urls,
            insecure=resolved_portainer_webhook_insecure,
            has_api_auth=has_portainer_api_auth,
        )

    # --- Post-deploy: register with centralized Caddy proxy ----------------
    resolved_public_domain = str(os.getenv(ENV_PUBLIC_DOMAIN) or "").strip()
    if not resolved_public_domain:
        resolved_public_domain = read_deploy_key(repo_root=repo_root, key=ENV_PUBLIC_DOMAIN)

    resolved_web_port = str(os.getenv(ENV_WEB_PORT) or "").strip()
    if not resolved_web_port:
        resolved_web_port = read_deploy_key(repo_root=repo_root, key=ENV_WEB_PORT)
    if not resolved_web_port:
        resolved_web_port = "8081"

    resolved_caddy_proxy_dir = str(os.getenv(ENV_CADDY_PROXY_DIR) or "").strip()
    if not resolved_caddy_proxy_dir:
        resolved_caddy_proxy_dir = read_deploy_key(repo_root=repo_root, key=ENV_CADDY_PROXY_DIR)

    if resolved_public_domain:
        # Derive service name from the Portainer stack name (which matches the
        # primary compose service name by convention).
        service_name = registration_service_name

        # Determine Caddyfile path. Prefer explicit override; otherwise inspect
        # the running central-proxy container mount source.
        if resolved_caddy_proxy_dir:
            proxy_repo_dir = Path(resolved_caddy_proxy_dir)
            caddyfile_path = str(proxy_repo_dir / "docker" / "proxy" / "Caddyfile")
        else:
            discovered_caddyfile_path = discover_remote_caddyfile_path(host=resolved_host)
            if discovered_caddyfile_path:
                caddyfile_path = discovered_caddyfile_path
            else:
                proxy_repo_dir = remote_dir.parent / "camera-storage-viewer"
                caddyfile_path = str(proxy_repo_dir / "docker" / "proxy" / "Caddyfile")

        log_step("Registering with centralized Caddy proxy", icon="🔒")
        try:
            caddy_register.ensure_caddy_registration(
                ssh_host=resolved_host,
                domain=resolved_public_domain,
                service=service_name,
                port=resolved_web_port,
                caddyfile_path=caddyfile_path,
            )
        except Exception as exc:
            log_info(f"Caddy registration failed: {exc}", icon="⚠️")
            log_info(
                "Deployment completed, but public HTTPS routing may be unavailable until Caddy is fixed.",
                icon="⚠️",
            )
    else:
        log_info("PUBLIC_DOMAIN not set — skipping Caddy registration.", icon="⚠️")

    if storage_registrations:
        if resolved_storage_manager_api_url:
            log_step("Registering storage-manager labels via API", icon="🧹")
            register_storage_manager_registrations(
                api_url=resolved_storage_manager_api_url,
                registrations=storage_registrations,
            )
        else:
            if stack_includes_storage_manager:
                log_info(
                    "Storage-manager labels detected; STORAGE_MANAGER_API_URL not set. Registration remains active via storage-manager Docker label auto-discovery (no action required).",
                    icon="ℹ️",
                )
            else:
                log_info(
                    "Storage-manager labels were detected but STORAGE_MANAGER_API_URL is not set; skipping registration.",
                    icon="⚠️",
                )

    print("[ubuntu-deploy] ✅ Done.")


if __name__ == "__main__":
    main()
