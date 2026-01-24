from __future__ import annotations

import math


def normalize_aci_memory_gb(memory_gb: float) -> float:
    # ACI requires memoryInGB to be a multiple of 0.1.
    if memory_gb <= 0:
        raise ValueError(f"memory_gb must be > 0, got {memory_gb!r}")
    return math.ceil(memory_gb * 10) / 10


def generate_deploy_yaml(
    *,
    name: str,
    location: str,
    image: str,
    registry_server: str | None,
    registry_username: str | None,
    registry_password: str | None,
    identity_id: str,
    identity_client_id: str | None,
    identity_tenant_id: str | None,
    storage_name: str,
    storage_key: str,
    kv_name: str,
    dns_label: str,
    cpu_cores: float,
    memory_gb: float,
    data_share_name: str,
    ftp_port: int = 21,
    ftp_passive_port_min: int = 50000,
    ftp_passive_port_max: int = 50100,
) -> str:
    app_memory_gb = normalize_aci_memory_gb(memory_gb)

    def indent(level: int, text: str) -> str:
        return " " * level + text

    if ftp_port < 1 or ftp_port > 65535:
        raise ValueError(f"ftp_port must be 1-65535, got {ftp_port}")
    if ftp_passive_port_min < 1 or ftp_passive_port_min > 65535:
        raise ValueError(f"ftp_passive_port_min must be 1-65535, got {ftp_passive_port_min}")
    if ftp_passive_port_max < 1 or ftp_passive_port_max > 65535:
        raise ValueError(f"ftp_passive_port_max must be 1-65535, got {ftp_passive_port_max}")
    if ftp_passive_port_max < ftp_passive_port_min:
        raise ValueError("ftp_passive_port_max must be >= ftp_passive_port_min")

    lines: list[str] = [
        "apiVersion: '2023-05-01'",
        f"location: {location}",
        f"name: {name}",
        "identity:",
        indent(2, "type: UserAssigned"),
        indent(2, "userAssignedIdentities:"),
        indent(4, f"'{identity_id}': {{}}"),
        "properties:",
    ]

    if registry_server or registry_username or registry_password:
        if not (registry_server and registry_username and registry_password):
            raise ValueError("registry_server/registry_username/registry_password must all be set when using registry credentials")

        lines += [
            indent(2, "imageRegistryCredentials:"),
            indent(4, f"- server: {registry_server}"),
            indent(6, f"username: {registry_username}"),
            indent(6, f"password: '{registry_password}'"),
        ]

    lines += [
        indent(2, "containers:"),
        indent(4, f"- name: {name}"),
        indent(6, "properties:"),
        indent(8, f"image: {image}"),
        indent(8, "ports:"),
        indent(10, f"- port: {ftp_port}"),
        indent(12, "protocol: TCP"),
    ]

    for p in range(ftp_passive_port_min, ftp_passive_port_max + 1):
        lines += [
            indent(10, f"- port: {p}"),
            indent(12, "protocol: TCP"),
        ]

    lines += [
        indent(8, "resources:"),
        indent(10, "requests:"),
        indent(12, f"cpu: {cpu_cores}"),
        indent(12, f"memoryInGB: {app_memory_gb}"),
        indent(8, "environmentVariables:"),
        indent(10, "- name: AZURE_KEYVAULT_URI"),
        indent(12, f"value: 'https://{kv_name}.vault.azure.net/'"),
    ]

    if identity_client_id:
        lines += [
            indent(10, "- name: AZURE_CLIENT_ID"),
            indent(12, f"value: '{identity_client_id}'"),
        ]
    if identity_tenant_id:
        lines += [
            indent(10, "- name: AZURE_TENANT_ID"),
            indent(12, f"value: '{identity_tenant_id}'"),
        ]

    # Data volume mount (Azure Files)
    lines += [
        indent(8, "volumeMounts:"),
        indent(10, "- name: data-volume"),
        indent(12, "mountPath: /data"),
        "",
        indent(2, "osType: Linux"),
        indent(2, "restartPolicy: Always"),
        indent(2, "ipAddress:"),
        indent(4, "type: Public"),
        indent(4, f"dnsNameLabel: {dns_label}"),
        indent(4, "ports:"),
        indent(6, f"- port: {ftp_port}"),
    ]

    for p in range(ftp_passive_port_min, ftp_passive_port_max + 1):
        lines.append(indent(6, f"- port: {p}"))

    lines += [
        "",
        indent(2, "volumes:"),
        indent(4, "- name: data-volume"),
        indent(6, "azureFile:"),
        indent(8, f"shareName: {data_share_name}"),
        indent(8, f"storageAccountName: {storage_name}"),
        indent(8, f"storageAccountKey: {storage_key}"),
    ]

    return "\n".join(lines) + "\n"
