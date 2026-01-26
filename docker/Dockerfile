FROM python:3.12-slim

WORKDIR /app

# Runtime dependencies for Key Vault MI fetch, web viewer, and thumbnail generation
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
  && rm -rf /var/lib/apt/lists/*

# Install runtime Python deps (FTP + viewer)
COPY requirements.runtime.txt /tmp/requirements.runtime.txt
COPY requirements.web.runtime.txt /tmp/requirements.web.runtime.txt
RUN pip install --no-cache-dir \
  -r /tmp/requirements.runtime.txt \
  -r /tmp/requirements.web.runtime.txt

# Copy only what we need to run
COPY src /app/src
COPY docker/azure_start.sh /usr/local/bin/azure_start.sh
RUN chmod +x /usr/local/bin/azure_start.sh

ENV PYTHONPATH=/app \
    OUT_DIR=/data \
  WEB_PORT=8081 \
    RUNTIME_ENV_PATH=/app/.env

# FTP control + passive ports + viewer port
EXPOSE 21 50000-50100 8081

ENTRYPOINT ["/usr/local/bin/azure_start.sh"]
CMD ["python", "-m", "src.ftp_server"]
