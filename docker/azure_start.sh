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

    # If a user-assigned Managed Identity is configured, ACI typically requires selecting it
    # explicitly. The deploy script passes AZURE_CLIENT_ID for that identity.
    if [ -n "${AZURE_CLIENT_ID:-}" ]; then
        TOKEN_URL="${TOKEN_URL}&client_id=${AZURE_CLIENT_ID}"
    fi

    # NOTE: explicitly bypass proxies for IMDS and retry transient errors.
    TOKEN_JSON="$(
        curl -sS --fail --noproxy '*' \
          --connect-timeout 2 --max-time 10 \
          --retry 5 --retry-all-errors --retry-delay 1 \
          -H 'Metadata: true' "${TOKEN_URL}" 2>&1
    )"
    CURL_STATUS=$?
    if [ ${CURL_STATUS} -ne 0 ]; then
        echo "[azure_start] ERROR: Failed to fetch Managed Identity token from IMDS (curl_exit=${CURL_STATUS})" >&2
        # Safe to log: should contain only curl error text.
        printf "%s\n" "${TOKEN_JSON}" | head -c 800 >&2 || true
        echo >&2
        exit 1
    fi
    if [ -z "${TOKEN_JSON}" ]; then
        echo "[azure_start] ERROR: IMDS token response was empty" >&2
        exit 1
    fi

    ACCESS_TOKEN="$(
        printf "%s" "${TOKEN_JSON}" | python -c "
import json, sys
raw = sys.stdin.read() or ''
try:
    doc = json.loads(raw)
except Exception:
    print('[azure_start] ERROR: IMDS token response was not valid JSON', file=sys.stderr)
    # On success the response contains an access_token; we're in a failure path so it's safe
    # to print a small prefix to help debugging.
    print(f'[azure_start] IMDS raw prefix: {raw[:200]!r}', file=sys.stderr)
    raise SystemExit(1)

tok = doc.get('access_token')
if not tok:
    # Avoid logging secrets; token is absent here. Log only error fields if present.
    err = doc.get('error')
    desc = doc.get('error_description')
    keys = ','.join(sorted([k for k in doc.keys() if isinstance(k, str)]))
    msg = f'[azure_start] ERROR: Managed Identity token response missing access_token (keys={keys})'
    if err or desc:
        msg += f' (error={err!s} desc={desc!s})'
    print(msg, file=sys.stderr)
    raise SystemExit(1)

print(tok)
" 
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

    # Also fetch runtime secrets (.env.secrets content) if uploaded as a separate KV secret.
    # FTP credentials (FTP_PASSWORD, FTP_USERS_JSON) live in .env.secrets and are uploaded
    # to Key Vault as the 'env-secrets' secret by the deploy engine.
    SECRETS_SECRET_NAME="${AZURE_SECRETS_SECRET_NAME:-env-secrets}"
    SECRETS_URL="${VAULT_URL}/secrets/${SECRETS_SECRET_NAME}?api-version=7.4"

    SECRETS_JSON="$(curl -sS --noproxy '*' \
      --connect-timeout 2 --max-time 10 \
      --retry 3 --retry-all-errors --retry-delay 1 \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" "${SECRETS_URL}" 2>&1)" || true

    SECRETS_VALUE="$(python -c 'import json,sys; print(json.loads(sys.stdin.read()).get("value",""))' <<EOF
${SECRETS_JSON}
EOF
)" 2>/dev/null || true

    if [ -n "${SECRETS_VALUE}" ]; then
        # Append secrets to the runtime env file so load_ftp_config() sees all values.
        printf "\n%s" "${SECRETS_VALUE}" >> "${ENV_OUT_PATH}"
        echo "[azure_start] Successfully appended runtime secrets (${SECRETS_SECRET_NAME}) to ${ENV_OUT_PATH}"
    else
        echo "[azure_start] No runtime secrets found (${SECRETS_SECRET_NAME}), continuing without"
    fi
else
    echo "[azure_start] No AZURE_KEYVAULT_URI set; skipping Key Vault fetch"
fi

echo "[azure_start] Starting application..."
exec "$@"
