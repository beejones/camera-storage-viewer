#!/bin/sh
set -eu

UPSTREAM="${UPSTREAM:-web:8081}"
PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-}"
ACME_EMAIL="${ACME_EMAIL:-}"

if [ -n "$PUBLIC_DOMAIN" ]; then
  cat > /etc/caddy/Caddyfile <<EOF
{
    email ${ACME_EMAIL}
}

${PUBLIC_DOMAIN} {
    reverse_proxy ${UPSTREAM}
}
EOF
else
  cat > /etc/caddy/Caddyfile <<EOF
:80 {
    auto_https off
    reverse_proxy ${UPSTREAM}
}
EOF
fi

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
