from scripts.deploy.csv_deploy_yaml_helpers import generate_deploy_yaml_web_caddy


def test_generate_deploy_yaml_web_caddy_structure():
    yaml_out = generate_deploy_yaml_web_caddy(
        name="test-container",
        location="westeurope",
        image="ghcr.io/test/web:latest",
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
        cpu_cores=1.0,
        memory_gb=2.0,
        data_share_name="data",
        public_domain="camera-storage-viewer.zenia.eu",
        acme_email="admin@example.com",
        web_port=8081,
    )

    # public ports
    assert "- port: 80" in yaml_out
    assert "- port: 443" in yaml_out

    # web container and caddy sidecar
    assert "- name: test-container-web" in yaml_out
    assert "- name: test-container-caddy" in yaml_out

    # Caddy image and embedded config wiring
    assert "image: caddy:2" in yaml_out
    assert "caddy run" in yaml_out
