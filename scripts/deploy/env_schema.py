"""Deterministic environment variable/secret schema for deployment.

This module is the single source of truth for:
- which keys exist (vars vs secrets)
- where they are expected to live (.env, .env.deploy, GitHub Actions vars/secrets)
- whether they are mandatory and/or have defaults

Design goals:
- No heuristic classification (no regex guessing).
- No backwards compatibility aliases.
- Fail fast with clear error messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping

from dotenv import dotenv_values


class EnvTarget(str, Enum):
    DOTENV_RUNTIME = "dotenv_runtime"  # `.env`
    DOTENV_DEPLOY = "dotenv_deploy"  # `.env.deploy`
    GH_ACTIONS_VAR = "gh_actions_var"  # GitHub Actions variable
    GH_ACTIONS_SECRET = "gh_actions_secret"  # GitHub Actions secret
    KEYVAULT_SECRET = "keyvault_secret"  # Azure Key Vault secret


class KeyVaultSecretName(str, Enum):
    ENV = "env"  # stores full runtime `.env` content


class VarsEnum(str, Enum):
    # Azure identity / subscription (OIDC)
    AZURE_CLIENT_ID = "AZURE_CLIENT_ID"
    AZURE_TENANT_ID = "AZURE_TENANT_ID"
    AZURE_SUBSCRIPTION_ID = "AZURE_SUBSCRIPTION_ID"
    AZURE_OIDC_APP_NAME = "AZURE_OIDC_APP_NAME"

    # Azure deployment config
    AZURE_RESOURCE_GROUP = "AZURE_RESOURCE_GROUP"
    AZURE_LOCATION = "AZURE_LOCATION"
    AZURE_CONTAINER_NAME = "AZURE_CONTAINER_NAME"
    AZURE_DNS_LABEL = "AZURE_DNS_LABEL"

    # Domain / TLS
    PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    ACME_EMAIL = "ACME_EMAIL"

    # Sidecar images
    CADDY_IMAGE = "CADDY_IMAGE"

    # Image / registry
    CONTAINER_IMAGE = "CONTAINER_IMAGE"
    GHCR_PRIVATE = "GHCR_PRIVATE"
    GHCR_USERNAME = "GHCR_USERNAME"

    # Defaults / sizing
    DEFAULT_CPU_CORES = "DEFAULT_CPU_CORES"
    DEFAULT_MEMORY_GB = "DEFAULT_MEMORY_GB"

    # Runtime
    BASIC_AUTH_USER = "BASIC_AUTH_USER"

    # Runtime (camera FTP)
    OUT_DIR = "OUT_DIR"
    WEB_PORT = "WEB_PORT"
    THUMBNAIL_GENERATION = "THUMBNAIL_GENERATION"
    FTP_BIND_HOST = "FTP_BIND_HOST"
    FTP_PORT = "FTP_PORT"
    FTP_PUBLIC_HOST = "FTP_PUBLIC_HOST"
    FTP_PASSIVE_PORT_MIN = "FTP_PASSIVE_PORT_MIN"
    FTP_PASSIVE_PORT_MAX = "FTP_PASSIVE_PORT_MAX"
    FTP_PERMIT_FOREIGN_ADDRESSES = "FTP_PERMIT_FOREIGN_ADDRESSES"
    FTP_INCOMING_DIR = "FTP_INCOMING_DIR"
    FTP_SPOOL_DIR = "FTP_SPOOL_DIR"
    FTP_USERNAME = "FTP_USERNAME"
    FTP_CAMERA_ID = "FTP_CAMERA_ID"
    FTP_DEV_DEFAULTS = "FTP_DEV_DEFAULTS"
    RETENTION_DAYS = "RETENTION_DAYS"


class SecretsEnum(str, Enum):
    # Image / registry
    GHCR_TOKEN = "GHCR_TOKEN"

    # Runtime
    BASIC_AUTH_HASH = "BASIC_AUTH_HASH"
    VIEWER_AUTH_TOKEN = "VIEWER_AUTH_TOKEN"

    # Runtime (camera FTP)
    FTP_PASSWORD = "FTP_PASSWORD"
    FTP_USERS_JSON = "FTP_USERS_JSON"

    # App/runtime secret (optional; user-defined)
    APP_SECRET = "APP_SECRET"

    # GitHub Actions meta-secret (not a container env var, but required by CI wiring)
    RUNTIME_ENV_DOTENV = "RUNTIME_ENV_DOTENV"


@dataclass(frozen=True)
class EnvKeySpec:
    key: VarsEnum | SecretsEnum
    mandatory: bool
    default: str | None = None
    targets: frozenset[EnvTarget] = frozenset()
    keyvault_secret_name: KeyVaultSecretName | None = None


class EnvValidationError(ValueError):
    def __init__(self, *, context: str, problems: list[str]):
        super().__init__("; ".join(problems))
        self.context = context
        self.problems = problems

    def format(self) -> str:
        lines = [f"[env] validation failed: {self.context}"]
        for p in self.problems:
            lines.append(f"- {p}")
        return "\n".join(lines)


RUNTIME_SCHEMA: tuple[EnvKeySpec, ...] = (
    EnvKeySpec(
        key=VarsEnum.BASIC_AUTH_USER,
        mandatory=False,
        default="admin",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=SecretsEnum.BASIC_AUTH_HASH,
        mandatory=False,
        targets=frozenset({EnvTarget.DOTENV_RUNTIME, EnvTarget.GH_ACTIONS_SECRET}),
    ),
    EnvKeySpec(
        key=SecretsEnum.APP_SECRET,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),

    # Camera FTP runtime configuration
    EnvKeySpec(
        key=VarsEnum.OUT_DIR,
        mandatory=False,
        default="/data",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.WEB_PORT,
        mandatory=False,
        default="8081",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=SecretsEnum.VIEWER_AUTH_TOKEN,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.THUMBNAIL_GENERATION,
        mandatory=False,
        default="true",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.FTP_BIND_HOST,
        mandatory=False,
        default="0.0.0.0",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.FTP_PORT,
        mandatory=False,
        default="21",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.FTP_PUBLIC_HOST,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.FTP_PASSIVE_PORT_MIN,
        mandatory=False,
        default="50000",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.FTP_PASSIVE_PORT_MAX,
        mandatory=False,
        default="50003",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.FTP_PERMIT_FOREIGN_ADDRESSES,
        mandatory=False,
        default="true",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.FTP_INCOMING_DIR,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.FTP_SPOOL_DIR,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.FTP_USERNAME,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=SecretsEnum.FTP_PASSWORD,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.FTP_CAMERA_ID,
        mandatory=False,
        default="camera1",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.FTP_DEV_DEFAULTS,
        mandatory=False,
        default="false",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=SecretsEnum.FTP_USERS_JSON,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
    EnvKeySpec(
        key=VarsEnum.RETENTION_DAYS,
        mandatory=False,
        default="30",
        targets=frozenset({EnvTarget.DOTENV_RUNTIME}),
    ),
)


DEPLOY_SCHEMA: tuple[EnvKeySpec, ...] = (
    EnvKeySpec(
        key=VarsEnum.AZURE_CLIENT_ID,
        mandatory=False,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.AZURE_TENANT_ID,
        mandatory=False,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.AZURE_SUBSCRIPTION_ID,
        mandatory=False,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.AZURE_OIDC_APP_NAME,
        mandatory=True,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.AZURE_RESOURCE_GROUP,
        mandatory=False,
        default="camera-storage-viewer-rg",
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.AZURE_LOCATION,
        mandatory=False,
        default="westeurope",
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.AZURE_CONTAINER_NAME,
        mandatory=False,
        default="camera-storage-viewer",
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.AZURE_DNS_LABEL,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.PUBLIC_DOMAIN,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.ACME_EMAIL,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.CADDY_IMAGE,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.CONTAINER_IMAGE,
        mandatory=True,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.GHCR_PRIVATE,
        mandatory=False,
        default="false",
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.GHCR_USERNAME,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=SecretsEnum.GHCR_TOKEN,
        mandatory=False,
        default=None,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_SECRET}),
    ),
    EnvKeySpec(
        key=VarsEnum.DEFAULT_CPU_CORES,
        mandatory=False,
        default="1.0",
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.DEFAULT_MEMORY_GB,
        mandatory=False,
        default="2.0",
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=VarsEnum.BASIC_AUTH_USER,
        mandatory=False,
        default="admin",
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_VAR}),
    ),
    EnvKeySpec(
        key=SecretsEnum.BASIC_AUTH_HASH,
        mandatory=False,
        targets=frozenset({EnvTarget.DOTENV_DEPLOY, EnvTarget.GH_ACTIONS_SECRET}),
    ),
    # GitHub Actions expects this secret to exist to materialize `.env` in CI.
    EnvKeySpec(
        key=SecretsEnum.RUNTIME_ENV_DOTENV,
        mandatory=True,
        targets=frozenset({EnvTarget.GH_ACTIONS_SECRET}),
    ),
)


def _schema_keys(schema: Iterable[EnvKeySpec]) -> set[str]:
    return {spec.key.value for spec in schema}


def parse_dotenv_file(path: Path) -> dict[str, str]:
    """Parse dotenv file strictly.

    - Comments are ignored.
    - Keys are preserved even if they have empty values (""), so we can detect unknown/forbidden keys.
    """
    kv: dict[str, str] = {}
    raw = dotenv_values(path)
    for k, v in raw.items():
        if k is None:
            continue
        key = str(k).strip()
        if not key:
            continue
        val = "" if v is None else str(v).strip()
        kv[key] = val
    return kv


def _format_dotenv_value(value: str) -> str:
    # Keep it simple and deterministic; the values we write back (GUIDs) are safe unquoted.
    # If needed in the future, add quoting/escaping here.
    return str(value)


def write_dotenv_values(*, path: Path, updates: Mapping[str, str], create: bool = False) -> None:
    """Update (or create) a dotenv file in-place.

    - Preserves existing lines/comments.
    - Replaces existing KEY=... lines for keys in `updates`.
    - Appends missing keys at the end in sorted order.

    This is intentionally minimal: it avoids reformatting unrelated content.
    """
    if not updates:
        return

    if not path.exists():
        if not create:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        header = "\n".join(
            [
                "# Generated/updated by scripts/deploy/azure_deploy_container.py",
                "",
            ]
        )
        path.write_text(header)

    original_lines = path.read_text().splitlines()
    remaining = {k: _format_dotenv_value(v) for k, v in updates.items() if str(v).strip()}
    if not remaining:
        return

    out: list[str] = []
    for line in original_lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in line:
            out.append(line)
            continue

        key = line.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
            continue

        out.append(line)

    if remaining:
        if out and out[-1].strip() != "":
            out.append("")
        for key in sorted(remaining.keys()):
            out.append(f"{key}={remaining[key]}")

    path.write_text("\n".join(out) + "\n")


def validate_known_keys(schema: Iterable[EnvKeySpec], kv: Mapping[str, str], *, context: str) -> None:
    allowed = _schema_keys(schema)
    unknown = sorted([k for k in kv.keys() if k not in allowed])
    if unknown:
        raise EnvValidationError(
            context=context,
            problems=["Unknown key(s): " + ", ".join(unknown)],
        )


def apply_defaults(schema: Iterable[EnvKeySpec], kv: dict[str, str]) -> dict[str, str]:
    out = dict(kv)
    for spec in schema:
        if spec.key.value in out and str(out.get(spec.key.value) or "").strip():
            continue
        if spec.default is None:
            continue
        out[spec.key.value] = spec.default
    return out


def validate_required(schema: Iterable[EnvKeySpec], kv: Mapping[str, str], *, context: str) -> None:
    missing: list[str] = []
    for spec in schema:
        if not spec.mandatory:
            continue
        val = str(kv.get(spec.key.value) or "").strip()
        if not val:
            missing.append(spec.key.value)
    if missing:
        raise EnvValidationError(context=context, problems=["Missing mandatory key(s): " + ", ".join(sorted(missing))])


def truthy(val: str | None) -> bool:
    v = str(val or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def validate_cross_field_rules(*, deploy_kv: Mapping[str, str], context: str) -> None:
    """Extra validation for rules that can't be expressed with (mandatory/default) alone."""
    problems: list[str] = []

    if truthy(deploy_kv.get(VarsEnum.GHCR_PRIVATE.value)):
        # For private GHCR images, username+token are required for ACI pull.
        if not str(deploy_kv.get(VarsEnum.GHCR_USERNAME.value) or "").strip():
            problems.append(f"{VarsEnum.GHCR_USERNAME.value} is required when {VarsEnum.GHCR_PRIVATE.value}=true")
        if not str(deploy_kv.get(SecretsEnum.GHCR_TOKEN.value) or "").strip():
            problems.append(f"{SecretsEnum.GHCR_TOKEN.value} is required when {VarsEnum.GHCR_PRIVATE.value}=true")

    if problems:
        raise EnvValidationError(context=context, problems=problems)


def filter_schema_by_targets(schema: Iterable[EnvKeySpec], *, include: set[EnvTarget]) -> list[EnvKeySpec]:
    return [spec for spec in schema if spec.targets.intersection(include)]


def get_spec(schema: Iterable[EnvKeySpec], key: VarsEnum | SecretsEnum) -> EnvKeySpec:
    for spec in schema:
        if spec.key == key:
            return spec
    raise KeyError(key)
