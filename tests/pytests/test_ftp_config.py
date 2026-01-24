from __future__ import annotations

import json

import pytest

from src.config import load_ftp_config


def test_load_ftp_config_single_user() -> None:
    cfg = load_ftp_config(
        {
            "FTP_USERNAME": "cam1",
            "FTP_PASSWORD": "secret",
            "FTP_CAMERA_ID": "front",
            "OUT_DIR": "/tmp/out",
        }
    )

    assert cfg.port == 21
    assert cfg.users[0].camera_id == "front"
    assert cfg.users[0].username == "cam1"


def test_load_ftp_config_users_json_list() -> None:
    cfg = load_ftp_config(
        {
            "FTP_USERS_JSON": json.dumps(
                [
                    {"camera_id": "front", "username": "front", "password": "p1"},
                    {"camera_id": "back", "username": "back", "password": "p2"},
                ]
            )
        }
    )

    assert {u.camera_id for u in cfg.users} == {"front", "back"}


def test_load_ftp_config_requires_users() -> None:
    with pytest.raises(ValueError):
        load_ftp_config({})
