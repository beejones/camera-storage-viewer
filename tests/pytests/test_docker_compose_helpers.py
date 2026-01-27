import pytest
from pathlib import Path
import os
import sys

# Add scripts/deploy to path
sys.path.append(str(Path(__file__).parents[2] / "scripts/deploy"))

import docker_compose_helpers as compose_helpers

@pytest.fixture
def mock_compose_file(tmp_path):
    compose_content = """
services:
  app:
    image: my-app:latest
    ports:
      - "8080:8080"
    environment:
      - PORT=9090
      - HOST=${HOST:-localhost}
      - KEY=${KEY}
    build:
      context: ./app-context

  db:
    image: postgres:15
    environment:
      POSTGRES_PASSWORD: secret
"""
    d = tmp_path / "docker-compose.yml"
    d.write_text(compose_content)
    return tmp_path

def test_load_docker_compose_config(mock_compose_file):
    config = compose_helpers.load_docker_compose_config(mock_compose_file)
    assert "services" in config
    assert "app" in config["services"]

def test_interpolation(mock_compose_file, monkeypatch):
    monkeypatch.setenv("KEY", "my-secret-key")
    # HOST not set, should use default
    
    config = compose_helpers.load_docker_compose_config(mock_compose_file)
    app_env = config["services"]["app"]["environment"]
    
    # Check simple list parsing handling in helper? 
    # Our helper returns raw parsed yaml. user of helper handles list/dict.
    # PyYAML parses "HOST=${HOST:-localhost}" as a string in the list.
    
    # Let's test the helper function meant to interpolate:
    # Actually load_docker_compose_config calls interpolate_dict internally.
    
    # We need to verify if the list items changed.
    # Original: "- HOST=${HOST:-localhost}"
    # Parsed by yaml: ["HOST=${HOST:-localhost}"]
    # Interpolated: ["HOST=localhost"]
    
    assert "HOST=localhost" in app_env
    assert "KEY=my-secret-key" in app_env

def test_get_service_config(mock_compose_file):
    config = compose_helpers.load_docker_compose_config(mock_compose_file)
    app = compose_helpers.get_service_config(config, "app")
    assert app["image"] == "my-app:latest"

def test_get_env_var(mock_compose_file):
    config = compose_helpers.load_docker_compose_config(mock_compose_file)
    app = compose_helpers.get_service_config(config, "app")
    
    assert compose_helpers.get_env_var(app, "PORT") == "9090"
    
    # In list format, our helper iterates.
    # If environment is a list: ["PORT=9090", ...]
    # yaml parses it as list of strings.
    
def test_get_ports(mock_compose_file):
    config = compose_helpers.load_docker_compose_config(mock_compose_file)
    app = compose_helpers.get_service_config(config, "app")
    ports = compose_helpers.get_ports(app)
    assert "8080:8080" in ports or "8080:8080" == ports[0]
    
    # Test dict format (long syntax)
    service_config = {"ports": [{"target": 8080, "published": 443}]}
    assert compose_helpers.get_ports(service_config) == [{"target": 8080, "published": 443}]

def test_get_build_context(mock_compose_file):
    config = compose_helpers.load_docker_compose_config(mock_compose_file)
    app = compose_helpers.get_service_config(config, "app")
    assert compose_helpers.get_build_context(app) == "./app-context"

def test_get_deploy_role(mock_compose_file):
    # Mock file doesn't have x-deploy-role yet, let's inject it for this test or update fixture
    # Updating fixture is cleaner, but let's just mock the dict for this specific function test
    service_config = {"x-deploy-role": "app"}
    assert compose_helpers.get_deploy_role(service_config) == "app"
    
    service_config = {"image": "foo"}
    assert compose_helpers.get_deploy_role(service_config) is None
