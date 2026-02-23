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
    public_domain: str,
    acme_email: str,
    basic_auth_user: str,
    basic_auth_hash: str,
    app_cpu_cores: float,
    app_memory_gb: float,
    share_workspace: str,
    data_share_name: str | None = None,
    caddy_data_share_name: str,
    caddy_config_share_name: str,
    caddy_image: str,
    caddy_cpu_cores: float,
    caddy_memory_gb: float,
    app_port: int,
    app_ports: list[int] | None = None,
    app_command: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    other_image: str | None = None,
    other_cpu_cores: float = 0.5,
    other_memory_gb: float = 0.5,
    restart_policy: str = "OnFailure",
    include_caddy: bool = True,
) -> str:
    restart_policy_norm = str(restart_policy or "").strip() or "OnFailure"
    if restart_policy_norm not in {"Always", "OnFailure", "Never"}:
        raise ValueError("restart_policy must be one of: Always, OnFailure, Never")

    app_memory_gb = normalize_aci_memory_gb(app_memory_gb)
    tls_memory_gb = normalize_aci_memory_gb(caddy_memory_gb)
    other_mem_gb_norm = normalize_aci_memory_gb(other_memory_gb)
    
    # Normalize app_ports. We always include app_port (legacy compatibility)
    all_app_ports = sorted(list(set(([app_port] if app_port else []) + (app_ports or []))))

    def indent(level: int, text: str) -> str:
        return " " * level + text

    caddy_cmd = ""
    if include_caddy:
        # Caddyfile generated inline
        caddy_cmd = "\n".join(
            [
                "set -eu",
                "mkdir -p /config/caddy",
                "cat > /config/caddy/Caddyfile <<'CADDY'",
                "{",
                "  email {$ACME_EMAIL}",
                "}",
                "",
                f"{public_domain} {{",
                "  log",
                "  encode zstd gzip",
                '  header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"',
                "",
                "  # Single Basic Auth layer for all routes",
                "  basic_auth /* {",
                "    {$BASIC_AUTH_USER} {$BASIC_AUTH_HASH}",
                "  }",
                "",
                "  # Proxy to app",
                f"  reverse_proxy http://127.0.0.1:{app_port} {{",
                "    header_up Upgrade {http.request.header.Upgrade}",
                "    header_up Connection {http.request.header.Connection}",
                "  }",
                "}",
                "CADDY",
                "",
                "# Run Caddy with the mounted /data for certs",
                "exec caddy run --config /config/caddy/Caddyfile --adapter caddyfile",
            ]
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
        indent(8, "ports:"),
    ]
    
    for p in all_app_ports:
        lines += [
            indent(10, f"- port: {p}"),
            indent(12, "protocol: TCP"),
        ]

    lines += [
        indent(8, "resources:"),
        indent(10, "requests:"),
        indent(12, f"cpu: {app_cpu_cores}"),
        indent(12, f"memoryInGB: {app_memory_gb}"),
        indent(8, "environmentVariables:"),
        indent(10, "- name: AZURE_KEYVAULT_URI"),
        indent(12, f"value: 'https://{kv_name}.vault.azure.net/'"),
    ]

    # Inject CODE_SERVER_PORT for legacy apps that expect it.
    # Only if app_port is set AND we don't already have WEB_PORT (which is the new way)
    if app_port and (not extra_env or "WEB_PORT" not in extra_env):
        lines += [
            indent(10, "- name: CODE_SERVER_PORT"),
            indent(12, f"value: '{app_port}'"),
        ]

    # Inject extra env variables (e.g. WEB_PORT from Compose)
    if extra_env:
        for k, v in extra_env.items():
            lines += [
                indent(10, f"- name: {k}"),
                indent(12, f"value: '{v}'"),
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

    # NOTE: In ACI, setting `command:` overrides the image ENTRYPOINT.
    # Our Dockerfile ENTRYPOINT runs /usr/local/bin/azure_start.sh to fetch
    # Key Vault secrets and then exec the app process. When the compose-driven
    # deploy sets an explicit app command (e.g. uvicorn/python), we must prefix
    # it with azure_start.sh to preserve that behavior.
    if app_command:
        effective_app_command = list(app_command)
        if effective_app_command[0] != "/usr/local/bin/azure_start.sh":
            effective_app_command = ["/usr/local/bin/azure_start.sh", *effective_app_command]

        lines += [indent(8, "command:")]
        for arg in effective_app_command:
            lines += [indent(10, f"- {arg}")]

    # Entrypoint: default from Dockerfile (/usr/local/bin/azure_start.sh)
    lines += [
        indent(8, "volumeMounts:"),
        indent(10, "- name: workspace-volume"),
        indent(12, "mountPath: /home/coder/workspace"),
    ]

    # Optionally mount durable storage under /data for the app.
    if data_share_name:
        lines += [
            indent(10, "- name: data-volume"),
            indent(12, "mountPath: /data"),
        ]

    if include_caddy:
        lines += [
            "",
            indent(4, "- name: tls-proxy"),
            indent(6, "properties:"),
            indent(8, f"image: {caddy_image}"),
            indent(8, "ports:"),
            indent(10, "- port: 80"),
            indent(12, "protocol: TCP"),
            indent(10, "- port: 443"),
            indent(12, "protocol: TCP"),
            indent(8, "resources:"),
            indent(10, "requests:"),
            indent(12, f"cpu: {caddy_cpu_cores}"),
            indent(12, f"memoryInGB: {tls_memory_gb}"),
            indent(8, "environmentVariables:"),
            indent(10, "- name: PUBLIC_DOMAIN"),
            indent(12, f"value: '{public_domain}'"),
            indent(10, "- name: ACME_EMAIL"),
            indent(12, f"value: '{acme_email}'"),
            indent(10, "- name: FALLBACK_DOMAIN"),
            indent(12, f"value: '{dns_label}.{location}.azurecontainer.io'"),
            indent(10, "- name: BASIC_AUTH_USER"),
            indent(12, f"value: '{basic_auth_user}'"),
            indent(10, "- name: BASIC_AUTH_HASH"),
            indent(12, f"secureValue: '{basic_auth_hash}'"),
            indent(8, "command:"),
            indent(10, "- sh"),
            indent(10, "- -lc"),
            indent(10, "- |"),
        ]

        for line in caddy_cmd.splitlines():
            lines.append(indent(12, line))

        lines += [
            indent(8, "volumeMounts:"),
            indent(10, "- name: caddy-data"),
            indent(12, "mountPath: /data"),
            indent(10, "- name: caddy-config"),
            indent(12, "mountPath: /config"),
        ]

    # Container group properties
    lines += [
        "",
        indent(2, "osType: Linux"),
        indent(2, f"restartPolicy: {restart_policy_norm}"),
        indent(2, "ipAddress:"),
        indent(4, "type: Public"),
        indent(4, f"dnsNameLabel: {dns_label}"),
        indent(4, "ports:"),
    ]

    if include_caddy:
        lines += [
            indent(6, "- port: 80"),
            indent(6, "- port: 443"),
        ]
    else:
        if len(all_app_ports) > 5:
            raise ValueError(f"ACI supports max 5 public ports; got {len(all_app_ports)}: {all_app_ports}")
        for p in all_app_ports:
            lines += [indent(6, f"- port: {p}")]

    lines += [
        "",
        indent(2, "volumes:"),
        indent(4, "- name: workspace-volume"),
        indent(6, "azureFile:"),
        indent(8, f"shareName: {share_workspace}"),
        indent(8, f"storageAccountName: {storage_name}"),
        indent(8, f"storageAccountKey: {storage_key}"),
    ]

    if data_share_name:
        lines += [
            indent(4, "- name: data-volume"),
            indent(6, "azureFile:"),
            indent(8, f"shareName: {data_share_name}"),
            indent(8, f"storageAccountName: {storage_name}"),
            indent(8, f"storageAccountKey: {storage_key}"),
        ]

    if include_caddy:
        lines += [
            indent(4, "- name: caddy-data"),
            indent(6, "azureFile:"),
            indent(8, f"shareName: {caddy_data_share_name}"),
            indent(8, f"storageAccountName: {storage_name}"),
            indent(8, f"storageAccountKey: {storage_key}"),
            indent(4, "- name: caddy-config"),
            indent(6, "azureFile:"),
            indent(8, f"shareName: {caddy_config_share_name}"),
            indent(8, f"storageAccountName: {storage_name}"),
            indent(8, f"storageAccountKey: {storage_key}"),
        ]
    
    if other_image:
        insert_idx = -1

        if include_caddy:
            for i, line in enumerate(lines):
                if line.strip() == "- name: tls-proxy":
                    insert_idx = i
                    break
            if insert_idx == -1:
                raise ValueError("Could not find insertion point for 'other' container (expected '- name: tls-proxy').")
        else:
            # Insert before container group properties (osType), right after the app container.
            for i, line in enumerate(lines):
                if line.strip() == "osType: Linux":
                    insert_idx = i - 1  # keep a blank line before group properties
                    break
            if insert_idx < 0:
                raise ValueError("Could not find insertion point for 'other' container (expected 'osType: Linux').")

        other_block = [
            indent(4, "- name: other"),
            indent(6, "properties:"),
            indent(8, f"image: {other_image}"),
            indent(8, "resources:"),
            indent(10, "requests:"),
            indent(12, f"cpu: {other_cpu_cores}"),
            indent(12, f"memoryInGB: {other_mem_gb_norm}"),
            indent(8, "volumeMounts:"),
            indent(10, "- name: workspace-volume"),
            indent(12, "mountPath: /home/coder/workspace"),
            "",
        ]
        lines = lines[:insert_idx] + other_block + lines[insert_idx:]

    return "\n".join(lines) + "\n"
