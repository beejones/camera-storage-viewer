from __future__ import annotations

import base64
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
    ftp_passive_port_max: int = 50003,
    command: list[str] | None = None,
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

    # Azure Container Instances limitation: maximum 5 public IP ports per container group.
    # FTP requires 1 control port + N passive ports, so the passive range must be small in ACI.
    ports_unique = [ftp_port] + list(range(ftp_passive_port_min, ftp_passive_port_max + 1))
    ports_unique = sorted(set(ports_unique))
    if len(ports_unique) > 5:
        raise ValueError(
            "ACI container groups support at most 5 public ports; "
            f"requested {len(ports_unique)} ports. "
            "Reduce FTP_PASSIVE_PORT_MAX so the passive range is <= 4 ports "
            "(e.g. 50000-50003), or deploy somewhere that supports larger port ranges."
        )

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
    ]

    # ACI `command` overrides Docker ENTRYPOINT; include azure_start.sh explicitly
    # so the runtime env is fetched from Key Vault.
    effective_command = command or [
        "/usr/local/bin/azure_start.sh",
        "python",
        "-m",
        "src.ftp_server",
    ]
    if effective_command:
        lines.append(indent(8, "command:"))
        for part in effective_command:
            # Quote to keep YAML parsing stable.
            lines.append(indent(10, f"- '{part}'"))

    lines += [
        indent(8, "ports:"),
        indent(10, f"- port: {ftp_port}"),
        indent(12, "protocol: TCP"),
    ]

    for p in range(ftp_passive_port_min, ftp_passive_port_max + 1):
        if p == ftp_port:
            continue
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
        if p == ftp_port:
            continue
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


def generate_deploy_yaml_web(
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
    web_port: int = 80,
    web_command: list[str] | None = None,
) -> str:
    """Generate an ACI deployment YAML for the viewer web API/UI.

    Notes:
    - ACI provides plain TCP/HTTP exposure only; TLS termination must be handled externally
      (e.g. Cloudflare/Front Door) unless you run a proxy sidecar.
    - The web image in this repo supports fetching a runtime .env from Key Vault via azure_start.sh.
    """

    app_memory_gb = normalize_aci_memory_gb(memory_gb)

    def indent(level: int, text: str) -> str:
        return " " * level + text

    if web_port < 1 or web_port > 65535:
        raise ValueError(f"web_port must be 1-65535, got {web_port}")

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
            raise ValueError(
                "registry_server/registry_username/registry_password must all be set when using registry credentials"
            )

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
    ]

    if web_command:
        lines.append(indent(8, "command:"))
        for part in web_command:
            lines.append(indent(10, f"- '{part}'"))

    lines += [
        indent(8, "ports:"),
        indent(10, f"- port: {web_port}"),
        indent(12, "protocol: TCP"),
        indent(8, "resources:"),
        indent(10, "requests:"),
        indent(12, f"cpu: {cpu_cores}"),
        indent(12, f"memoryInGB: {app_memory_gb}"),
        indent(8, "environmentVariables:"),
        indent(10, "- name: AZURE_KEYVAULT_URI"),
        indent(12, f"value: 'https://{kv_name}.vault.azure.net/'"),
        indent(10, "- name: WEB_PORT"),
        indent(12, f"value: '{web_port}'"),
        indent(10, "- name: OUT_DIR"),
        indent(12, "value: '/data'"),
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
        indent(6, f"- port: {web_port}"),
        "",
        indent(2, "volumes:"),
        indent(4, "- name: data-volume"),
        indent(6, "azureFile:"),
        indent(8, f"shareName: {data_share_name}"),
        indent(8, f"storageAccountName: {storage_name}"),
        indent(8, f"storageAccountKey: {storage_key}"),
    ]

    return "\n".join(lines) + "\n"


def generate_deploy_yaml_web_caddy(
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
    public_domain: str | None,
    acme_email: str | None = None,
    caddy_image: str = "caddy:2",
    web_port: int = 8081,
    web_command: list[str] | None = None,
) -> str:
    """Generate an ACI deployment YAML for the viewer web API/UI behind Caddy.

    Exposes:
    - 80/tcp and 443/tcp publicly (Caddy)
    - proxies to localhost:8081 inside the container group

    This is the recommended shape when you want a custom domain with HTTPS.
    """

    app_memory_gb = normalize_aci_memory_gb(memory_gb)

    def indent(level: int, text: str) -> str:
        return " " * level + text

    public_domain = str(public_domain or "").strip() or None
    if web_port < 1 or web_port > 65535:
        raise ValueError(f"web_port must be 1-65535, got {web_port}")

    # Generate a minimal Caddyfile and embed it as base64 to avoid YAML quoting issues.
    #
    # We include a plain-HTTP site for the ACI-provided FQDN so you can test routing
    # before DNS for the custom domain is switched.
    aci_fqdn = f"{dns_label}.{location}.azurecontainer.io"
    caddyfile = [
        "{",
    ]
    if acme_email and str(acme_email).strip():
        caddyfile.append(f"\temail {str(acme_email).strip()}")
    caddyfile += [
        "}",
        "",
        f"http://{aci_fqdn} {{",
        f"\treverse_proxy 127.0.0.1:{web_port}",
        "}",
        "",
    ]

    if public_domain:
        caddyfile += [
            f"{public_domain} {{",
            f"\treverse_proxy 127.0.0.1:{web_port}",
            "}",
            "",
        ]
    caddyfile_text = "\n".join(caddyfile)
    caddyfile_b64 = base64.b64encode(caddyfile_text.encode("utf-8")).decode("ascii")
    caddy_cmd = (
        "echo "
        + caddyfile_b64
        + " | base64 -d > /etc/caddy/Caddyfile && caddy run --config /etc/caddy/Caddyfile --adapter caddyfile"
    )

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
            raise ValueError(
                "registry_server/registry_username/registry_password must all be set when using registry credentials"
            )

        lines += [
            indent(2, "imageRegistryCredentials:"),
            indent(4, f"- server: {registry_server}"),
            indent(6, f"username: {registry_username}"),
            indent(6, f"password: '{registry_password}'"),
        ]

    # Two containers in one group: web app + caddy
    lines += [
        indent(2, "containers:"),
        indent(4, f"- name: {name}-web"),
        indent(6, "properties:"),
        indent(8, f"image: {image}"),
    ]

    if web_command:
        lines.append(indent(8, "command:"))
        for part in web_command:
            lines.append(indent(10, f"- '{part}'"))

    lines += [
        indent(8, "ports:"),
        indent(10, f"- port: {web_port}"),
        indent(12, "protocol: TCP"),
        indent(8, "resources:"),
        indent(10, "requests:"),
        indent(12, f"cpu: {cpu_cores}"),
        indent(12, f"memoryInGB: {app_memory_gb}"),
        indent(8, "environmentVariables:"),
        indent(10, "- name: AZURE_KEYVAULT_URI"),
        indent(12, f"value: 'https://{kv_name}.vault.azure.net/'"),
        indent(10, "- name: WEB_PORT"),
        indent(12, f"value: '{web_port}'"),
        indent(10, "- name: OUT_DIR"),
        indent(12, "value: '/data'"),
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

    lines += [
        indent(8, "volumeMounts:"),
        indent(10, "- name: data-volume"),
        indent(12, "mountPath: /data"),
        indent(4, f"- name: {name}-caddy"),
        indent(6, "properties:"),
        indent(8, f"image: {caddy_image}"),
        indent(8, "ports:"),
        indent(10, "- port: 80"),
        indent(12, "protocol: TCP"),
    ]

    if public_domain:
        lines += [
            indent(10, "- port: 443"),
            indent(12, "protocol: TCP"),
        ]

    lines += [
        indent(8, "command:"),
        indent(10, "- sh"),
        indent(10, "- -lc"),
        indent(10, f"- '{caddy_cmd}'"),
        indent(8, "resources:"),
        indent(10, "requests:"),
        indent(12, "cpu: 0.5"),
        indent(12, "memoryInGB: 0.5"),
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
        indent(6, "- port: 80"),
    ]

    if public_domain:
        lines.append(indent(6, "- port: 443"))

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
