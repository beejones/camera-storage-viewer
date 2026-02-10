#!/bin/sh
# Azure startup script (lightweight)
# Fetches runtime .env from Key Vault using Managed Identity + Key Vault REST.

set -eu

echo "[azure_start] Starting container..."

ENV_OUT_PATH="${RUNTIME_ENV_PATH:-/app/.env}"
ENV_SECRET_NAME="${AZURE_ENV_SECRET_NAME:-env}"

if [ -n "${AZURE_KEYVAULT_URI:-}" ]; then
    echo "[azure_start] Fetching '${ENV_SECRET_NAME}' from Key Vault: ${AZURE_KEYVAULT_URI}"

    # Managed Identity token for Key Vault
    TOKEN_URL="http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net"
    TOKEN_JSON="$(curl -sS --fail -H 'Metadata: true' "${TOKEN_URL}")"
    ACCESS_TOKEN="$(python -c 'import json,sys; print(json.loads(sys.stdin.read())["access_token"])' <<EOF
${TOKEN_JSON}
EOF
)"

    # Normalize vault URL (ensure no trailing slash)
    VAULT_URL="${AZURE_KEYVAULT_URI%/}"
    SECRET_URL="${VAULT_URL}/secrets/${ENV_SECRET_NAME}?api-version=7.4"

    SECRET_JSON="$(curl -sS --fail -H "Authorization: Bearer ${ACCESS_TOKEN}" "${SECRET_URL}")"
    SECRET_VALUE="$(python -c 'import json,sys; print(json.loads(sys.stdin.read()).get("value",""))' <<EOF
${SECRET_JSON}
EOF
)"

    if [ -z "${SECRET_VALUE}" ]; then
        echo "[azure_start] ERROR: Key Vault secret '${ENV_SECRET_NAME}' is empty or missing 'value'" >&2
        exit 1
    fi

    mkdir -p "$(dirname "${ENV_OUT_PATH}")"
    printf "%s" "${SECRET_VALUE}" > "${ENV_OUT_PATH}"
    chmod 600 "${ENV_OUT_PATH}" || true
    echo "[azure_start] Successfully wrote runtime env to ${ENV_OUT_PATH}"
else
    echo "[azure_start] No AZURE_KEYVAULT_URI set; skipping Key Vault fetch"
fi

echo "[azure_start] Starting application..."
exec "$@"
