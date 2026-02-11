import sys
from types import SimpleNamespace

from scripts.deploy import azure_deploy_container


def test_ensure_infra_always_provisions_data_share_with_quota(monkeypatch):
    calls: list[tuple[str, int]] = []

    def ensure_file_share_exists(*, account_name: str, share_name: str, resource_group: str, quota_gb: int):
        calls.append((share_name, quota_gb))

    fake_helpers = SimpleNamespace(
        run_az_command=lambda *args, **kwargs: "",
        ensure_resource_group=lambda **kwargs: None,
        ensure_managed_identity=lambda **kwargs: {"principalId": "pid"},
        ensure_storage_account=lambda **kwargs: {"name": "stg"},
        ensure_key_vault=lambda **kwargs: {"name": "kv"},
        ensure_role_assignments=lambda **kwargs: None,
        ensure_file_share_exists=ensure_file_share_exists,
    )

    monkeypatch.setitem(sys.modules, "azure_deploy_container_helpers", fake_helpers)

    azure_deploy_container._ensure_infra_quota_scoped_to_data_share(
        resource_group="rg",
        location="westeurope",
        container_name="camera-storage-viewer",
        identity_name="id",
        keyvault_name="kv",
        storage_name="stg",
        shares=[
            "camera-storage-viewer-workspace",
            "camera-storage-viewer-caddy-data",
            "camera-storage-viewer-caddy-config",
        ],
        file_share_quota_gb=100,
    )

    assert ("camera-storage-viewer-data", 100) in calls
    assert ("camera-storage-viewer-workspace", 5) in calls
    assert ("camera-storage-viewer-caddy-data", 5) in calls
    assert ("camera-storage-viewer-caddy-config", 5) in calls
