FROM python:3.12-slim

WORKDIR /app

# Runtime dependencies for Key Vault MI fetch and debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
  && rm -rf /var/lib/apt/lists/*

# Install only runtime Python deps (FTP server + dotenv)
COPY requirements.runtime.txt /tmp/requirements.runtime.txt
RUN pip install --no-cache-dir -r /tmp/requirements.runtime.txt

# Copy only what we need to run
COPY src /app/src
COPY docker/azure_start.sh /usr/local/bin/azure_start.sh
RUN chmod +x /usr/local/bin/azure_start.sh

ENV PYTHONPATH=/app \
    OUT_DIR=/data \
    RUNTIME_ENV_PATH=/app/.env

# FTP control + passive ports (range is configured via env, but image exposes defaults)
EXPOSE 21 50000-50100

ENTRYPOINT ["/usr/local/bin/azure_start.sh"]
CMD ["python", "-m", "src.ftp_server"]
