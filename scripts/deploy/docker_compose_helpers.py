import os
import re
import yaml
from pathlib import Path
from typing import Any, Dict, Optional, Union

# Regex to match ${VAR:-default} or ${VAR}
INTERPOLATION_PATTERN = re.compile(r'\$\{([^}:]+)(?::-([^}]*))?\}')

def interpolate_value(value: str) -> str:
    """
    Interpolates environment variables in a string.
    Supports ${VAR} and ${VAR:-default}.
    """
    if not isinstance(value, str):
        return value

    def replace_match(match):
        var_name = match.group(1)
        default_value = match.group(2)
        env_val = os.getenv(var_name)
        if env_val is not None:
            return env_val
        return default_value if default_value is not None else ""

    return INTERPOLATION_PATTERN.sub(replace_match, value)

def interpolate_dict(data: Any) -> Any:
    """Recursively interpolates strings in a dictionary or list."""
    if isinstance(data, dict):
        return {k: interpolate_dict(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [interpolate_dict(v) for v in data]
    elif isinstance(data, str):
        return interpolate_value(data)
    else:
        return data

def load_docker_compose_config(cwd: Path) -> Dict[str, Any]:
    """
    Parses docker-compose.yml using PyYAML and interpolates variables.
    Returns the parsed configuration dictionary.
    """
    compose_path = cwd / "docker-compose.yml"
    if not compose_path.exists():
        raise FileNotFoundError(f"docker-compose.yml not found in {cwd}")

    try:
        with open(compose_path, "r") as f:
            raw_config = yaml.safe_load(f)
        
        # Interpolate variables
        config = interpolate_dict(raw_config)
        return config
    except yaml.YAMLError as e:
        raise RuntimeError(f"Failed to parse docker-compose.yml: {e}") from e

def get_service_config(compose_config: Dict[str, Any], service_name: str) -> Dict[str, Any]:
    """Retrieve the configuration for a specific service."""
    services = compose_config.get("services", {})
    if service_name not in services:
        raise ValueError(f"Service '{service_name}' not found in docker-compose list.")
    return services[service_name]

def get_env_var(service_config: Dict[str, Any], env_name: str) -> Optional[str]:
    """Get an environment variable value from a service config."""
    environment = service_config.get("environment", {})
    # Environment can be a dict or a list in docker-compose
    if isinstance(environment, dict):
        val = environment.get(env_name)
        return str(val) if val is not None else None
    elif isinstance(environment, list):
        for item in environment:
            if item.startswith(f"{env_name}="):
                return item.split("=", 1)[1]
    return None

def get_image(service_config: Dict[str, Any]) -> str:
    """Get the image name for a service."""
    return service_config.get("image", "")

def get_ports(service_config: Dict[str, Any]) -> list:
    """Get the exposed ports for a service."""
    # PyYAML parses "80:80" as string usually, but "80" might be int.
    # We normalize to a list of raw values (strings or ints or dicts if long syntax).
    ports = service_config.get("ports", [])
    if ports is None:
        return []
    return ports

def get_build_context(service_config: Dict[str, Any]) -> Optional[str]:
    """Get the build context path."""
    build = service_config.get("build")
    if not build:
        return None
    if isinstance(build, str):
        return build
    if isinstance(build, dict):
        return build.get("context")
    return None

def get_deploy_role(service_config: Dict[str, Any]) -> Optional[str]:
    """Get the x-deploy-role value (e.g. 'app', 'sidecar')."""
    return service_config.get("x-deploy-role")

def get_command(service_config: Dict[str, Any]) -> Optional[Union[str, list]]:
    """Get the command for a service."""
    return service_config.get("command")


def get_volumes(service_config: Dict[str, Any]) -> list[Any]:
    """Get the raw 'volumes' list for a service (Compose short or long syntax)."""
    vols = service_config.get("volumes", [])
    if vols is None:
        return []
    if not isinstance(vols, list):
        # Unexpected shape; keep it safe.
        return []
    return vols


def get_volume_targets(service_config: Dict[str, Any]) -> list[str]:
    """Return container target paths for service volume mounts.

    Supports Compose short syntax ("source:target[:mode]") and long syntax ({target: ...}).
    """

    targets: list[str] = []
    for v in get_volumes(service_config):
        if isinstance(v, str):
            # Short syntax: src:dst[:mode]
            parts = v.split(":")
            if len(parts) >= 2:
                target = str(parts[1]).strip()
                if target:
                    targets.append(target)
            continue

        if isinstance(v, dict):
            # Long syntax: {type: bind|volume, source: ..., target: ...}
            target = v.get("target") or v.get("destination")
            if target is not None:
                t = str(target).strip()
                if t:
                    targets.append(t)
            continue

    return targets


def normalize_command(command: Any) -> list[str]:
    """
    Normalizes a Compose command (string or list) into an ACI-compatible list of strings.
    If the command is a string and contains shell-like syntax ($, >) it may be wrapped in sh -lc.
    """
    if not command:
        return []
    if isinstance(command, list):
        return [str(c) for c in command]
    
    cmd_str = str(command).strip()
    if not cmd_str:
        return []

    # If interpolation is detected or complex shell chars, wrap in sh -lc
    # Actually, to avoid splitting issues with quotes/paths, we prefer wrapping ALL string commands.
    return ["sh", "-lc", cmd_str]


def detect_services_by_role(compose_config: Dict[str, Any]) -> Dict[str, list[str]]:
    """
    Returns a mapping of role -> [service_names] based on x-deploy-role.
    """
    services = compose_config.get("services", {})
    role_map: Dict[str, list[str]] = {}
    
    for name, config in services.items():
        role = get_deploy_role(config)
        if role:
            if role not in role_map:
                role_map[role] = []
            role_map[role].append(name)
            
    return role_map
