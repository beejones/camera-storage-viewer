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
        cpu_cores=1.0,
        memory_gb=2.0,
        data_share_name="data",
        ftp_port=21,
        ftp_passive_port_min=50000,
        ftp_passive_port_max=50002,
    )

    assert "name: test-container" in yaml_out
    assert "image: ghcr.io/test/image:latest" in yaml_out
    assert "- port: 21" in yaml_out
    assert "- port: 50000" in yaml_out
    assert "- port: 50001" in yaml_out
    assert "- port: 50002" in yaml_out
    assert "name: AZURE_KEYVAULT_URI" in yaml_out
