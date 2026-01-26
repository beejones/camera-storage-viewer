import pytest
from pathlib import Path
from scripts.deploy import docker_compose_helpers as compose_helpers

def test_detect_services_by_role_basic():
    config = {
        "services": {
            "web": {"x-deploy-role": "app"},
            "proxy": {"x-deploy-role": "sidecar"},
            "untagged": {}
        }
    }
    role_map = compose_helpers.detect_services_by_role(config)
    assert role_map["app"] == ["web"]
    assert role_map["sidecar"] == ["proxy"]
    assert "untagged" not in role_map

def test_detect_services_by_role_ambiguity():
    config = {
        "services": {
            "web1": {"x-deploy-role": "app"},
            "web2": {"x-deploy-role": "app"}
        }
    }
    role_map = compose_helpers.detect_services_by_role(config)
    assert len(role_map["app"]) == 2
    assert "web1" in role_map["app"]
    assert "web2" in role_map["app"]

def test_normalize_command_string():
    assert compose_helpers.normalize_command("python main.py") == ["sh", "-lc", "python main.py"]
    assert compose_helpers.normalize_command("python main.py --port ${PORT:-80}") == ["sh", "-lc", "python main.py --port ${PORT:-80}"]
    assert compose_helpers.normalize_command("echo hello > /tmp/out") == ["sh", "-lc", "echo hello > /tmp/out"]

def test_normalize_command_list():
    assert compose_helpers.normalize_command(["python", "main.py"]) == ["python", "main.py"]
    assert compose_helpers.normalize_command(["sh", "-c", "echo hello"]) == ["sh", "-c", "echo hello"]

def test_normalize_command_none():
    assert compose_helpers.normalize_command(None) == []
    assert compose_helpers.normalize_command("") == []
