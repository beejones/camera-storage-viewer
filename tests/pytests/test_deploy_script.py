from scripts.deploy.azure_deploy_container import generate_deploy_yaml

def test_generate_deploy_yaml_structure():
    yaml_out = generate_deploy_yaml(
        name="test-container",
        location="westeurope",
        image="ghcr.io/test/image:latest",
        registry_server="ghcr.io",
        registry_username="user",
        registry_password="token",
        identity_id="/subscriptions/s/resourcegroups/r/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id",
        identity_client_id="client-id",
        identity_tenant_id="tenant-id",
        storage_name="teststorage",
        storage_key="key",
        kv_name="test-kv",
        dns_label="test-label",
        public_domain="test.com",
        acme_email="admin@test.com",
        basic_auth_user="admin",
        basic_auth_hash="$2a$14$test",
        app_cpu_cores=1.0,
        app_memory_gb=2.0,
        share_workspace="workspace",
        caddy_data_share_name="caddy-data",
        caddy_config_share_name="caddy-config",
        caddy_image="caddy:2-alpine",
        caddy_cpu_cores=0.5,
        caddy_memory_gb=0.5,
        app_port=8080,
    )

    assert "name: test-container" in yaml_out
    assert "image: ghcr.io/test/image:latest" in yaml_out
    assert "port: 8080" in yaml_out # code-server port
    assert "name: CODE_SERVER_PORT" in yaml_out
    assert "name: BASIC_AUTH_USER" in yaml_out
    assert "secureValue: '$2a$14$test'" in yaml_out
