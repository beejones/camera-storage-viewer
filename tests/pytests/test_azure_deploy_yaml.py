import pytest
import yaml
import sys
from pathlib import Path

# Add scripts/deploy to path
sys.path.append(str(Path(__file__).parents[2] / "scripts/deploy"))

import azure_deploy_yaml_helpers as yaml_helpers

@pytest.fixture
def base_args():
    return {
        "name": "test-container",
        "location": "westeurope",
        "image": "app:latest",
        "registry_server": None,
        "registry_username": None,
        "registry_password": None,
        "identity_id": "id-123",
        "identity_client_id": "client-123",
        "identity_tenant_id": "tenant-123",
        "storage_name": "stg",
        "storage_key": "key",
        "kv_name": "kv",
        "dns_label": "dns",
        "public_domain": "example.com",
        "acme_email": "admin@example.com",
        "basic_auth_user": "admin",
        "basic_auth_hash": "hash",
        "app_cpu_cores": 1.0,
        "app_memory_gb": 2.0,
        "share_workspace": "workspace",
        "caddy_data_share_name": "caddy-data",
        "caddy_config_share_name": "caddy-config",
        "caddy_image": "caddy:2-alpine",
        "caddy_cpu_cores": 0.5,
        "caddy_memory_gb": 0.5,
        "app_port": 8080,
    }

def test_generate_deploy_yaml_defaults(base_args):
    """Test generating YAML with standard app + sidecar configuration."""
    yaml_str = yaml_helpers.generate_deploy_yaml(**base_args)
    data = yaml.safe_load(yaml_str)
    
    containers = data["properties"]["containers"]
    assert len(containers) == 2
    
    # Check App Container
    app = containers[0]
    assert app["name"] == "test-container"
    assert app["properties"]["image"] == "app:latest"
    assert app["properties"]["resources"]["requests"]["cpu"] == 1.0
    assert app["properties"]["resources"]["requests"]["memoryInGB"] == 2.0
    
    # Check Caddy Container
    caddy = containers[1]
    assert caddy["name"] == "tls-proxy"
    assert caddy["properties"]["image"] == "caddy:2-alpine"
    assert caddy["properties"]["resources"]["requests"]["cpu"] == 0.5
    assert caddy["properties"]["resources"]["requests"]["memoryInGB"] == 0.5

def test_generate_deploy_yaml_with_other(base_args):
    """Test generating YAML with an additional 'other' container."""
    base_args.update({
        "other_image": "other:latest",
        "other_cpu_cores": 0.25,
        "other_memory_gb": 0.5
    })
    
    yaml_str = yaml_helpers.generate_deploy_yaml(**base_args)
    data = yaml.safe_load(yaml_str)
    
    containers = data["properties"]["containers"]
    assert len(containers) == 3
    
    # Find containers by name
    names = [c["name"] for c in containers]
    assert "test-container" in names
    assert "tls-proxy" in names
    assert "other" in names
    
    # Verify Other Container
    other = next(c for c in containers if c["name"] == "other")
    assert other["properties"]["image"] == "other:latest"
    assert other["properties"]["resources"]["requests"]["cpu"] == 0.25
    assert other["properties"]["resources"]["requests"]["memoryInGB"] == 0.5 # Normalized to 0.5? Logic uses normalize_aci_memory_gb

def test_normalization(base_args):
    """Test that memory is normalized to nearest 0.1 GB."""
    base_args["app_memory_gb"] = 1.12
    yaml_str = yaml_helpers.generate_deploy_yaml(**base_args)
    data = yaml.safe_load(yaml_str)
    
    app = data["properties"]["containers"][0]
    # 1.12 -> ceil(1.12 * 10) / 10 = ceil(11.2)/10 = 12/10 = 1.2
    assert app["properties"]["resources"]["requests"]["memoryInGB"] == 1.2
