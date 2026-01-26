from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_compose(tmp_path: Path) -> Path:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """services:
  web:
    x-deploy-role: app
    image: example/web:latest
    command: [\"sh\", \"-lc\", \"echo hello\"]
    environment:
      WEB_PORT: 8081
      TOKEN: ${TOKEN:-default}
    ports:
      - \"8081:8081\"

  caddy:
    x-deploy-role: sidecar
    image: caddy:2

  ftp:
    x-deploy-role: ftp
    environment:
      FTP_PORT: 21
      FTP_PASSIVE_PORT_MIN: 50000
      FTP_PASSIVE_PORT_MAX: 50003
""",
        encoding="utf-8",
    )
    return compose


def test_derive_defaults_roles_and_interpolation(tmp_compose: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.deploy.docker_compose_helpers import derive_defaults

    monkeypatch.delenv("TOKEN", raising=False)

    defaults = derive_defaults(compose_path=tmp_compose)

    assert defaults.web_service == "web"
    assert defaults.caddy_service == "caddy"
    assert defaults.ftp_service == "ftp"

    assert defaults.caddy_image == "caddy:2"
    assert defaults.web_command == ["sh", "-lc", "echo hello"]
    assert defaults.web_port == 8081

    assert defaults.ftp_port == 21
    assert defaults.ftp_passive_port_min == 50000
    assert defaults.ftp_passive_port_max == 50003


def test_derive_defaults_cli_override(tmp_compose: Path) -> None:
    from scripts.deploy.docker_compose_helpers import derive_defaults

    # override to non-existent service names should not crash; defaults will be None-ish
    defaults = derive_defaults(
        compose_path=tmp_compose,
        compose_app_service="nope",
        compose_caddy_service="also-nope",
        compose_ftp_service="ftp",
    )

    assert defaults.ftp_service == "ftp"
    assert defaults.web_service == "nope"
    assert defaults.caddy_service == "also-nope"
