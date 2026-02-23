"""Helper functions for interacting with Portainer."""

from __future__ import annotations

import shlex
import requests

def _extract_webhook_token(payload: object) -> str:
    if isinstance(payload, dict):
        for key in ["Webhook", "webhook", "token", "Token", "webhookToken", "WebhookToken"]:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            token = _extract_webhook_token(value)
            if token:
                return token
    if isinstance(payload, list):
        for item in payload:
            token = _extract_webhook_token(item)
            if token:
                return token
    return ""


def _portainer_auth_headers(*, access_token: str) -> dict[str, str]:
    if access_token.strip():
        return {"X-API-Key": access_token.strip()}
    return {}


def extract_ssh_hostname(host: str) -> str:
    return host.split("@", 1)[1] if "@" in host else host


def resolve_portainer_webhook_url_via_api(
    *,
    host: str,
    https_port: int,
    insecure: bool,
    stack_name: str,
    endpoint_id: str,
    access_token: str,
    stack_file_content: str,
    ssh_run_fn,
) -> str:
    hostname = extract_ssh_hostname(host).strip()
    base_url = f"https://{hostname}:{https_port}"
    headers = _portainer_auth_headers(access_token=access_token)
    if not headers:
        return ""

    stacks_resp = requests.get(f"{base_url}/api/stacks", headers=headers, verify=not insecure, timeout=20)
    stacks_resp.raise_for_status()
    stacks_payload = stacks_resp.json()
    if not isinstance(stacks_payload, list):
        raise SystemExit("Unexpected Portainer /api/stacks response format")

    desired_name = stack_name.strip()

    endpoints_resp = requests.get(f"{base_url}/api/endpoints", headers=headers, verify=not insecure, timeout=20)
    endpoints_resp.raise_for_status()
    endpoints_payload = endpoints_resp.json()
    if not isinstance(endpoints_payload, list) or not endpoints_payload:
        raise SystemExit("No Portainer endpoints found")

    desired_endpoint = endpoint_id.strip()
    if not desired_endpoint:
        local_endpoint_id = ""
        for endpoint in endpoints_payload:
            if not isinstance(endpoint, dict):
                continue
            endpoint_name = str(endpoint.get("Name") or endpoint.get("name") or "").strip().lower()
            endpoint_url = str(endpoint.get("URL") or endpoint.get("url") or "").strip().lower()
            if endpoint_name == "local" or endpoint_url.startswith("unix://"):
                local_endpoint_id = str(endpoint.get("Id") or endpoint.get("id") or "").strip()
                if local_endpoint_id:
                    break
        if local_endpoint_id:
            desired_endpoint = local_endpoint_id
        else:
            first_endpoint = endpoints_payload[0]
            desired_endpoint = str(first_endpoint.get("Id") or first_endpoint.get("id") or "").strip()
    if not desired_endpoint:
        raise SystemExit("Unable to determine Portainer endpoint id; set PORTAINER_ENDPOINT_ID")

    matching_stacks: list[dict[str, object]] = []
    for stack in stacks_payload:
        if not isinstance(stack, dict):
            continue
        candidate_name = str(stack.get("Name") or stack.get("name") or "").strip()
        if candidate_name != desired_name:
            continue
        if desired_endpoint:
            candidate_endpoint = str(stack.get("EndpointId") or stack.get("endpointId") or "").strip()
            if candidate_endpoint != desired_endpoint:
                continue
        matching_stacks.append(stack)

    for stack in matching_stacks:
        stack_id = str(stack.get("Id") or stack.get("id") or "").strip()
        if not stack_id:
            continue
        stack_endpoint = str(stack.get("EndpointId") or stack.get("endpointId") or "").strip() or desired_endpoint
        delete_url = f"{base_url}/api/stacks/{stack_id}?endpointId={stack_endpoint}"
        delete_resp = requests.delete(delete_url, headers=headers, verify=not insecure, timeout=20)
        if not delete_resp.ok and int(delete_resp.status_code) != 404:
            details = str(delete_resp.text or "").strip().replace("\n", " ")[:300]
            raise SystemExit(f"Failed to remove existing Portainer stack '{desired_name}': {delete_resp.status_code} {details}")

    cleanup_names = _extract_container_names(stack_file_content)
    for container_name in cleanup_names:
        ssh_run_fn(f"docker rm -f {shlex.quote(container_name)} >/dev/null 2>&1 || true")

    create_url = f"{base_url}/api/stacks/create/standalone/string?endpointId={desired_endpoint}"
    create_payload = {
        "Name": desired_name,
        "StackFileContent": stack_file_content,
        "Env": [],
        "FromAppTemplate": False,
    }
    create_resp = requests.post(
        create_url,
        headers=headers,
        json=create_payload,
        verify=not insecure,
        timeout=30,
    )

    if not create_resp.ok:
        status_code = int(create_resp.status_code)
        details = str(create_resp.text or "").strip().replace("\n", " ")[:300]
        raise SystemExit(f"Failed to create Portainer stack '{desired_name}': {status_code} {details}")

    created_payload = create_resp.json()
    created_stack_id = str(created_payload.get("Id") or created_payload.get("id") or "").strip()
    created_webhook_token = _extract_webhook_token(created_payload)
    if created_webhook_token:
        return build_portainer_webhook_url(host=host, https_port=https_port, webhook_token=created_webhook_token)
    if created_stack_id:
        webhook_resp = requests.post(
            f"{base_url}/api/stacks/{created_stack_id}/webhooks?endpointId={desired_endpoint}",
            headers=headers,
            verify=not insecure,
            timeout=20,
        )
        if webhook_resp.ok:
            created_token = _extract_webhook_token(webhook_resp.json())
            if created_token:
                return build_portainer_webhook_url(host=host, https_port=https_port, webhook_token=created_token)

    return ""


def build_portainer_webhook_urls_from_token(*, host: str, https_port: int, webhook_token: str) -> list[str]:
    hostname = extract_ssh_hostname(host).strip()
    token = webhook_token.strip()
    return [
        f"https://{hostname}:{https_port}/api/stacks/webhooks/{token}",
        f"https://{hostname}:{https_port}/api/webhooks/{token}",
    ]


def build_portainer_webhook_url(*, host: str, https_port: int, webhook_token: str) -> str:
    return build_portainer_webhook_urls_from_token(host=host, https_port=https_port, webhook_token=webhook_token)[0]


def trigger_portainer_webhook(*, urls: list[str], insecure: bool, has_api_auth: bool) -> None:
    saw_404 = False
    last_error = ""
    for url in urls:
        try:
            response = requests.post(url, verify=not insecure, timeout=20)
        except requests.RequestException as exc:
            last_error = str(exc)
            continue

        if response.ok:
            return

        if int(response.status_code) == 404:
            saw_404 = True
            last_error = f"HTTP 404 from {url}"
            continue

        details = str(response.text or "").strip().replace("\n", " ")[:200]
        raise SystemExit(f"Portainer webhook trigger failed ({response.status_code}) at {url}: {details}")

    if saw_404:
        if not has_api_auth:
            raise SystemExit(
                "Portainer webhook returned 404 for all known endpoints and no Portainer API auth is configured. "
                "Set PORTAINER_ACCESS_TOKEN in .env.deploy.secrets "
                "so the script can auto-resolve/create the correct stack webhook."
            )
        raise SystemExit(
            "Portainer webhook returned 404 for all known endpoints even after API-assisted resolution. "
            "Verify PORTAINER_STACK_NAME/PORTAINER_ENDPOINT_ID and ensure the stack exists in Portainer."
        )
    if last_error:
        raise SystemExit(last_error)
    raise SystemExit("Portainer webhook trigger failed")


def _extract_container_names(stack_content: str) -> list[str]:
    import yaml
    payload = yaml.safe_load(stack_content)
    if not isinstance(payload, dict):
        return []
    services = payload.get("services")
    if not isinstance(services, dict):
        return []

    names: list[str] = []
    for service_payload in services.values():
        if not isinstance(service_payload, dict):
            continue
        container_name = str(service_payload.get("container_name") or "").strip()
        if container_name:
            names.append(container_name)
    return names

def portainer_ensure_running_remote_cmd(*, https_port: int) -> str:
    return (
        "docker network inspect caddy >/dev/null 2>&1 || docker network create caddy >/dev/null; "
        "if docker ps --format '{{.Names}}' | grep -Fxq portainer; then "
        "echo '[ubuntu-deploy] Portainer already running'; "
        "elif docker ps -a --format '{{.Names}}' | grep -Fxq portainer; then "
        "echo '[ubuntu-deploy] Starting existing Portainer container'; "
        "docker start portainer >/dev/null; "
        "else "
        "echo '[ubuntu-deploy] Creating Portainer container'; "
        "docker volume create portainer_data >/dev/null && "
        f"docker run -d --name portainer --restart=unless-stopped -p 8000:8000 -p {https_port}:9443 "
        "-v /var/run/docker.sock:/var/run/docker.sock -v portainer_data:/data "
        "portainer/portainer-ce:latest >/dev/null; "
        "fi; "
        "docker network connect caddy portainer >/dev/null 2>&1 || true"
    )
