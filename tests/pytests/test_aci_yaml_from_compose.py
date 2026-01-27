import pytest
from scripts.deploy import azure_deploy_yaml_helpers as yaml_helpers

def test_generate_yaml_with_command_and_ports():
    yaml_text = yaml_helpers.generate_deploy_yaml(
        name="test-cg",
        location="eastus",
        image="myrepo/myapp:latest",
        registry_server=None,
        registry_username=None,
        registry_password=None,
        identity_id="id123",
        identity_client_id=None,
        identity_tenant_id=None,
        storage_name="mystore",
        storage_key="mykey",
        kv_name="mykv",
        dns_label="myapp",
        public_domain="myapp.com",
        acme_email="admin@myapp.com",
        basic_auth_user="admin",
        basic_auth_hash="hash",
        app_cpu_cores=1.0,
        app_memory_gb=1.5,
        share_workspace="workspace",
        data_share_name="workspace",
        caddy_data_share_name="caddy-data",
        caddy_config_share_name="caddy-config",
        caddy_image="caddy:latest",
        caddy_cpu_cores=0.5,
        caddy_memory_gb=0.5,
        app_port=8080,
        app_ports=[8081],
        app_command=["python", "main.py", "--port", "8081"],
        extra_env={"WEB_PORT": "8081"}
    )
    
    # Verify both ports are exposed in the app container
    assert "- port: 8080" in yaml_text
    assert "- port: 8081" in yaml_text
    
    # Verify command is present
    assert "command:" in yaml_text
    assert "- /usr/local/bin/azure_start.sh" in yaml_text
    assert "- python" in yaml_text
    assert "- main.py" in yaml_text
    assert "- --port" in yaml_text
    assert "- 8081" in yaml_text
    
    # Verify extra env
    assert "name: WEB_PORT" in yaml_text
    assert "value: '8081'" in yaml_text
    
    # Verify legacy fallback is NOT present because WEB_PORT replaced it
    assert "name: CODE_SERVER_PORT" not in yaml_text

    # Verify /data is mounted for the app container when requested
    app_container_yaml = yaml_text.split("- name: tls-proxy")[0]
    assert "- name: data-volume" in app_container_yaml
    assert "mountPath: /data" in app_container_yaml
    assert "- name: data-volume" in yaml_text
    assert "shareName: workspace" in yaml_text

def test_generate_yaml_no_command_injects_nothing():
    yaml_text = yaml_helpers.generate_deploy_yaml(
        name="test-cg",
        location="eastus",
        image="myrepo/myapp:latest",
        registry_server=None,
        registry_username=None,
        registry_password=None,
        identity_id="id123",
        identity_client_id=None,
        identity_tenant_id=None,
        storage_name="mystore",
        storage_key="mykey",
        kv_name="mykv",
        dns_label="myapp",
        public_domain="myapp.com",
        acme_email="admin@myapp.com",
        basic_auth_user="admin",
        basic_auth_hash="hash",
        app_cpu_cores=1.0,
        app_memory_gb=1.5,
        share_workspace="workspace",
        caddy_data_share_name="caddy-data",
        caddy_config_share_name="caddy-config",
        caddy_image="caddy:latest",
        caddy_cpu_cores=0.5,
        caddy_memory_gb=0.5,
        app_port=8080
    )
    
    # No command should be in the YAML if app_command is None
    # We split by the next container's name to isolate the first one
    app_container_yaml = yaml_text.split("- name: tls-proxy")[0]
    assert "command:" not in app_container_yaml
