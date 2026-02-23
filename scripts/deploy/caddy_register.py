"""Register an app's domain with the centralized Caddy proxy on Ubuntu.

Ensures the proxy Caddyfile contains a reverse-proxy site block for the
deployed app.  Idempotent — skips if the block exists; appends and restarts
Caddy when missing.

Called by :func:`ubuntu_deploy.main` as a post-deploy step when
``PUBLIC_DOMAIN`` is set and external Caddy integration is detected.
"""
from __future__ import annotations

import logging
import re
import shlex
import subprocess


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Container name (from docker/proxy/docker-compose.yml).
DEFAULT_CADDY_CONTAINER = "central-proxy"
LOG_PREFIX = "[CADDY-REGISTER]"


logger = logging.getLogger(__name__)

# Template for the site block.  Placeholders: {domain}, {service}, {port}.
SITE_BLOCK_TEMPLATE = """\

# -------------------------
# {domain} Route (auto-registered)
# -------------------------
{domain} {{
    tls {{$ACME_EMAIL}}
    encode gzip
    header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
    reverse_proxy {service}:{port}
}}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ssh_run(
    host: str,
    cmd: str,
    *,
    check: bool = True,
    capture: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Run *cmd* on *host* via SSH."""
    full = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        host,
        cmd,
    ]
    return subprocess.run(
        full,
        input=input_text,
        capture_output=capture,
        text=True,
        check=check,
    )


def _domain_present(caddyfile_text: str, domain: str) -> bool:
    """Return True if *domain* already has a site block."""
    pattern = re.compile(
        r"^(?!\s*#)\s*" + re.escape(domain) + r"\s*\{",
        re.MULTILINE,
    )
    return bool(pattern.search(caddyfile_text))


def _public_domain_placeholder_present(caddyfile_text: str) -> bool:
    """Return True when a site block uses {$PUBLIC_DOMAIN}."""
    pattern = re.compile(r"^(?!\s*#)\s*\{\$PUBLIC_DOMAIN\}\s*\{", re.MULTILINE)
    return bool(pattern.search(caddyfile_text))


def _remote_public_domain(*, ssh_host: str, caddy_container: str) -> str:
    """Read PUBLIC_DOMAIN from remote Caddy container env if available."""
    cmd = (
        f"docker inspect {shlex.quote(caddy_container)} "
        "--format '{{range .Config.Env}}{{println .}}{{end}}' "
        "| grep '^PUBLIC_DOMAIN=' | head -n1 | cut -d= -f2-"
    )
    result = _ssh_run(ssh_host, cmd, check=False)
    if result.returncode != 0:
        return ""
    return str(result.stdout or "").strip()


def _result_text(result: subprocess.CompletedProcess) -> str:
    stderr = str(result.stderr or "").strip()
    stdout = str(result.stdout or "").strip()
    return stderr or stdout


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ensure_caddy_registration(
    *,
    ssh_host: str,
    domain: str,
    service: str,
    port: str | int,
    caddyfile_path: str,
    caddy_container: str = DEFAULT_CADDY_CONTAINER,
    dry_run: bool = False,
) -> bool:
    """Ensure *domain* has a site block in the remote Caddyfile.

    Returns True if the block was added, False if already present.
    Raises on SSH or validation failure.
    """
    port = str(port)

    # 1. Read current Caddyfile from the host  ───────────────────────────
    result = _ssh_run(ssh_host, f"cat {shlex.quote(caddyfile_path)}", check=False)
    if result.returncode != 0:
        # Caddyfile not found — proxy stack not deployed yet.  Skip silently.
        logger.warning(
            "%s Caddyfile not found at %s; proxy stack may not be deployed yet. Skipping registration.",
            LOG_PREFIX,
            caddyfile_path,
        )
        return False

    caddyfile_text = result.stdout

    # 2. Already registered?  ────────────────────────────────────────────
    if _domain_present(caddyfile_text, domain):
        logger.info("%s %s already registered", LOG_PREFIX, domain)
        return False

    # Special case: the base Caddyfile may already define {$PUBLIC_DOMAIN}.
    # If that placeholder resolves to this domain in the running proxy
    # container, skip appending a literal duplicate site block.
    if _public_domain_placeholder_present(caddyfile_text):
        resolved_public_domain = _remote_public_domain(
            ssh_host=ssh_host,
            caddy_container=caddy_container,
        )
        if resolved_public_domain and resolved_public_domain == domain:
            logger.info(
                "%s %s already covered by {$PUBLIC_DOMAIN} placeholder",
                LOG_PREFIX,
                domain,
            )
            return False

    # 3. Build the site block  ───────────────────────────────────────────
    block = SITE_BLOCK_TEMPLATE.format(domain=domain, service=service, port=port)

    if dry_run:
        logger.info("%s [dry-run] Would append to %s", LOG_PREFIX, caddyfile_path)
        logger.debug("%s [dry-run] Appended block:\n%s", LOG_PREFIX, block)
        return True

    # 4. Append to host Caddyfile  ──────────────────────────────────────
    append_cmd = f"tee -a {shlex.quote(caddyfile_path)} > /dev/null"
    full = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
        ssh_host, append_cmd,
    ]
    try:
        subprocess.run(full, input=block, text=True, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        detail = str(exc.stderr or "").strip() or str(exc.stdout or "").strip()
        raise RuntimeError(
            f"Failed to append Caddy block on {ssh_host}: {detail}"
        )

    # 5. Verify the write  ──────────────────────────────────────────────
    result2 = _ssh_run(ssh_host, f"cat {shlex.quote(caddyfile_path)}")
    if not _domain_present(result2.stdout, domain):
        raise RuntimeError(
            f"Appended Caddy block for {domain} but it was not found on re-read"
        )

    # 6. Restart Caddy to pick up bind-mount changes & obtain cert  ─────
    logger.info("%s Restarting %s to pick up config", LOG_PREFIX, caddy_container)
    restart_result = _ssh_run(
        ssh_host,
        f"docker restart {shlex.quote(caddy_container)}",
        check=False,
    )
    if restart_result.returncode != 0:
        detail = _result_text(restart_result)
        raise RuntimeError(
            f"Failed to restart {caddy_container} on {ssh_host}: {detail}"
        )

    # Brief wait for Caddy to boot, then validate config inside container.
    validate_cmd = (
        f"sleep 3 && docker exec {shlex.quote(caddy_container)} "
        f"caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile"
    )
    validate_result = _ssh_run(ssh_host, validate_cmd, check=False)
    if validate_result.returncode != 0:
        detail = _result_text(validate_result)
        raise RuntimeError(
            "Caddy registration appended the route but config validation failed: "
            f"{detail}. Check remote logs with: docker logs {caddy_container}"
        )

    logger.info("%s Registered %s -> %s:%s", LOG_PREFIX, domain, service, port)
    return True
