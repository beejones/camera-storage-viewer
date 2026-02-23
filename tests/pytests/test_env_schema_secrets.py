from __future__ import annotations
from pathlib import Path
import pytest
from scripts.deploy.env_schema import (
    RUNTIME_SCHEMA,
    SECRETS_SCHEMA,
    EnvValidationError,
    SecretsEnum,
    VarsEnum,
    parse_dotenv_file,
    validate_known_keys,
    validate_required,
)

def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p

def test_secrets_schema_keys_not_in_runtime_schema() -> None:
    """Verify separate schemas have distinct keys."""
    runtime_keys = {spec.key for spec in RUNTIME_SCHEMA}
    secrets_keys = {spec.key for spec in SECRETS_SCHEMA}
    
    assert SecretsEnum.BASIC_AUTH_HASH in secrets_keys
    assert SecretsEnum.BASIC_AUTH_HASH not in runtime_keys
    assert SecretsEnum.APP_SECRET in secrets_keys
    assert SecretsEnum.APP_SECRET not in runtime_keys

def test_secrets_schema_validation(tmp_path: Path) -> None:
    """Validate .env.secrets content against SECRETS_SCHEMA."""
    p = _write(tmp_path / ".env.secrets", "BASIC_AUTH_HASH=$2a$14$test\nAPP_SECRET=xyz\n")
    kv = parse_dotenv_file(p)
    
    validate_known_keys(SECRETS_SCHEMA, kv, context="secrets")
    validate_required(SECRETS_SCHEMA, kv, context="secrets")
    
    assert kv[SecretsEnum.BASIC_AUTH_HASH.value] == "$2a$14$test"

def test_env_secrets_unknown_keys_fail(tmp_path: Path) -> None:
    """Verify unknown keys in .env.secrets are rejected."""
    p = _write(tmp_path / ".env.secrets", "BASIC_AUTH_HASH=foo\nUNKNOWN_SECRET=bar\n")
    kv = parse_dotenv_file(p)
    
    with pytest.raises(EnvValidationError) as excinfo:
        validate_known_keys(SECRETS_SCHEMA, kv, context="secrets")
    
    assert "Unknown key(s): UNKNOWN_SECRET" in str(excinfo.value)

def test_merged_validation(tmp_path: Path) -> None:
    """Verify validation works when merging runtime and secrets keys."""
    # Simulate loading both files
    runtime_kv = {VarsEnum.BASIC_AUTH_USER.value: "admin"}
    secrets_kv = {SecretsEnum.BASIC_AUTH_HASH.value: "hash"}
    
    merged_kv = {**runtime_kv, **secrets_kv}
    
    # Validation against combined schema should pass
    validate_known_keys(RUNTIME_SCHEMA + SECRETS_SCHEMA, merged_kv, context="merged")
    validate_required(RUNTIME_SCHEMA + SECRETS_SCHEMA, merged_kv, context="merged")
