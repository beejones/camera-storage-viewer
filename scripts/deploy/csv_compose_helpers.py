from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_INTERPOLATION_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def _interpolate_str(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default_value = match.group(2)
        env_val = os.getenv(var_name)
        if env_val is not None:
            return env_val
        return default_value if default_value is not None else ""

    return _INTERPOLATION_PATTERN.sub(replace, value)


def _interpolate_any(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _interpolate_any(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_interpolate_any(v) for v in data]
    if isinstance(data, str):
        return _interpolate_str(data)
    return data


def load_docker_compose_config(*, compose_path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(compose_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("docker/docker-compose.yml must be a mapping")
    return _interpolate_any(raw)


def get_services(compose_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    services = compose_config.get("services")
    if not isinstance(services, dict):
        raise ValueError("docker/docker-compose.yml missing services")
    out: dict[str, dict[str, Any]] = {}
    for name, svc in services.items():
        if isinstance(svc, dict):
            out[str(name)] = svc
    return out


def get_deploy_role(service_config: dict[str, Any]) -> str | None:
    role = service_config.get("x-deploy-role")
    if role is None:
        return None
    role_str = str(role).strip()
    return role_str or None


def get_image(service_config: dict[str, Any]) -> str | None:
    image = service_config.get("image")
    if image is None:
        return None
    image_str = str(image).strip()
    return image_str or None


def get_command(service_config: dict[str, Any]) -> list[str] | None:
    cmd = service_config.get("command")
    if cmd is None:
        return None
    if isinstance(cmd, str):
        # Compose string form = shell form
        return ["sh", "-lc", cmd]
    if isinstance(cmd, list):
        out: list[str] = []
        for part in cmd:
            if part is None:
                continue
            out.append(str(part))
        return out or None
    return None


def get_ports(service_config: dict[str, Any]) -> list[Any]:
    ports = service_config.get("ports")
    if ports is None:
        return []
    if isinstance(ports, list):
        return ports
    # tolerate invalid shapes
    return []


def get_env_var(service_config: dict[str, Any], env_name: str) -> str | None:
    env = service_config.get("environment")
    if env is None:
        return None
    if isinstance(env, dict):
        val = env.get(env_name)
        if val is None:
            return None
        return str(val)
    if isinstance(env, list):
        prefix = f"{env_name}="
        for item in env:
            if not isinstance(item, str):
                continue
            if item.startswith(prefix):
                return item.split("=", 1)[1]
    return None


def guess_container_port(*, service_config: dict[str, Any], env_var_names: list[str]) -> int | None:
    # 1) If an env var defines the port, prefer it.
    for name in env_var_names:
        val = get_env_var(service_config, name)
        if val is None:
            continue
        try:
            return int(str(val).strip())
        except ValueError:
            pass

    # 2) Otherwise, infer from ports.
    for p in get_ports(service_config):
        # long syntax: { target: 8081, published: 8081, protocol: tcp }
        if isinstance(p, dict):
            target = p.get("target")
            if target is None:
                continue
            try:
                return int(target)
            except ValueError:
                continue
        # short syntax string: "8081:8081" or "8081"
        if isinstance(p, str):
            s = p.strip()
            if not s:
                continue
            # ranges (e.g. 50000-50003:50000-50003) aren't meaningful for "the" app port
            if "-" in s and ":" in s:
                continue
            parts = s.split(":")
            try:
                return int(parts[-1])
            except ValueError:
                continue
        if isinstance(p, int):
            return p

    return None


@dataclass(frozen=True)
class ComposeDeployDefaults:
    web_service: str | None
    caddy_service: str | None
    ftp_service: str | None
    caddy_image: str | None
    web_command: list[str] | None
    web_port: int | None
    ftp_port: int | None
    ftp_passive_port_min: int | None
    ftp_passive_port_max: int | None


def derive_defaults(
    *,
    compose_path: Path,
    compose_app_service: str | None = None,
    compose_caddy_service: str | None = None,
    compose_ftp_service: str | None = None,
) -> ComposeDeployDefaults:
    cfg = load_docker_compose_config(compose_path=compose_path)
    services = get_services(cfg)

    # Resolve by explicit args > role > conventional name
    def find_by_role(role: str) -> str | None:
        for name, svc in services.items():
            if get_deploy_role(svc) == role:
                return name
        return None

    web_name = (compose_app_service or find_by_role("app") or ("web" if "web" in services else None))
    caddy_name = (compose_caddy_service or find_by_role("sidecar") or ("caddy" if "caddy" in services else None))
    ftp_name = (compose_ftp_service or find_by_role("ftp") or ("ftp" if "ftp" in services else None))

    web_cfg = services.get(web_name) if web_name else None
    caddy_cfg = services.get(caddy_name) if caddy_name else None
    ftp_cfg = services.get(ftp_name) if ftp_name else None

    caddy_image = get_image(caddy_cfg) if caddy_cfg else None
    web_command = get_command(web_cfg) if web_cfg else None

    web_port = guess_container_port(service_config=web_cfg, env_var_names=["WEB_PORT"]) if web_cfg else None

    ftp_port = guess_container_port(service_config=ftp_cfg, env_var_names=["FTP_PORT"]) if ftp_cfg else None
    ftp_passive_port_min = guess_container_port(service_config=ftp_cfg, env_var_names=["FTP_PASSIVE_PORT_MIN"]) if ftp_cfg else None
    ftp_passive_port_max = guess_container_port(service_config=ftp_cfg, env_var_names=["FTP_PASSIVE_PORT_MAX"]) if ftp_cfg else None

    return ComposeDeployDefaults(
        web_service=web_name,
        caddy_service=caddy_name,
        ftp_service=ftp_name,
        caddy_image=caddy_image,
        web_command=web_command,
        web_port=web_port,
        ftp_port=ftp_port,
        ftp_passive_port_min=ftp_passive_port_min,
        ftp_passive_port_max=ftp_passive_port_max,
    )
