from __future__ import annotations

from pathlib import Path

import pytest

from scripts.deploy.env_schema import (
    DEPLOY_SCHEMA,
    RUNTIME_SCHEMA,
    EnvValidationError,
    SecretsEnum,
    VarsEnum,
    apply_defaults,
    normalize_legacy_deploy_keys,
    parse_dotenv_file,
    truthy,
    validate_cross_field_rules,
    validate_known_keys,
    validate_required,
)


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_dotenv_preserves_empty_values(tmp_path: Path) -> None:
    p = _write(tmp_path / ".env", "BASIC_AUTH_USER=\nBASIC_AUTH_HASH=\n")
    kv = parse_dotenv_file(p)
    assert kv[VarsEnum.BASIC_AUTH_USER.value] == ""
    assert kv[SecretsEnum.BASIC_AUTH_HASH.value] == ""


def test_runtime_defaults_and_required(tmp_path: Path) -> None:
    p = _write(tmp_path / ".env", "BASIC_AUTH_HASH=$2a$14$test\n")
    kv = parse_dotenv_file(p)

    validate_known_keys(RUNTIME_SCHEMA, kv, context="runtime")
    kv = apply_defaults(RUNTIME_SCHEMA, kv)

    # default user applied
    assert kv[VarsEnum.BASIC_AUTH_USER.value] == "admin"

    # required hash present
    validate_required(RUNTIME_SCHEMA, kv, context="runtime")


def test_unknown_keys_fail(tmp_path: Path) -> None:
    p = _write(tmp_path / ".env", "NOT_A_KEY=1\n")
    kv = parse_dotenv_file(p)
    with pytest.raises(EnvValidationError):
        validate_known_keys(RUNTIME_SCHEMA, kv, context="runtime")


def test_alias_keys_fail(tmp_path: Path) -> None:
    p = _write(tmp_path / ".env.deploy", "IMAGE=ghcr.io/x/y:latest\n")
    kv = parse_dotenv_file(p)
    with pytest.raises(EnvValidationError):
        validate_known_keys(DEPLOY_SCHEMA, kv, context="deploy")


def test_legacy_deploy_keys_normalize(tmp_path: Path) -> None:
    p = _write(
        tmp_path / ".env.deploy",
        "CONTAINER_IMAGE=ghcr.io/x/y:latest\nDEFAULT_CPU_CORES=1.0\nDEFAULT_MEMORY_GB=2.0\n",
    )
    kv = parse_dotenv_file(p)
    normalized, warnings = normalize_legacy_deploy_keys(kv)

    assert warnings
    assert VarsEnum.APP_IMAGE.value in normalized
    assert VarsEnum.APP_CPU_CORES.value in normalized
    assert VarsEnum.APP_MEMORY_GB.value in normalized
    assert "CONTAINER_IMAGE" not in normalized
    assert "DEFAULT_CPU_CORES" not in normalized
    assert "DEFAULT_MEMORY_GB" not in normalized

    # Should now validate cleanly against the strict schema.
    validate_known_keys(DEPLOY_SCHEMA, normalized, context="deploy")


def test_truthy() -> None:
    assert truthy("true")
    assert truthy("1")
    assert not truthy("false")
    assert not truthy("")


def test_cross_field_rules_require_ghcr_creds(tmp_path: Path) -> None:
    # GHCR_PRIVATE=true requires GHCR_USERNAME and GHCR_TOKEN
    kv = {
        VarsEnum.GHCR_PRIVATE.value: "true",
        VarsEnum.GHCR_USERNAME.value: "",
        SecretsEnum.GHCR_TOKEN.value: "",
    }
    with pytest.raises(EnvValidationError):
        validate_cross_field_rules(deploy_kv=kv, context="deploy")

    kv2 = {
        VarsEnum.GHCR_PRIVATE.value: "true",
        VarsEnum.GHCR_USERNAME.value: "me",
        SecretsEnum.GHCR_TOKEN.value: "tok",
    }
    validate_cross_field_rules(deploy_kv=kv2, context="deploy")
