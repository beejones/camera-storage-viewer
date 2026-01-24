# code-server base container
# Provides VS Code in browser with Azure Key Vault integration

FROM codercom/code-server:latest

USER root

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    ca-certificates \
    supervisor \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install Azure CLI for Key Vault access
RUN curl -sL https://aka.ms/InstallAzureCLIDeb | bash

# Install Python dependencies (deploy scripts + app)
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Create workspace directory
RUN mkdir -p /home/coder/workspace && chown -R coder:coder /home/coder/workspace

# Copy repository code into the workspace (overridden by bind-mount in local dev)
COPY . /home/coder/workspace
RUN chown -R coder:coder /home/coder/workspace

# Also copy the app code into a non-mounted path so it remains available in ACI,
# where /home/coder/workspace is typically an Azure Files mount.
RUN mkdir -p /opt/app
COPY . /opt/app
RUN chown -R coder:coder /opt/app

# Copy supervisor config
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Install VS Code extensions
USER coder
RUN code-server --install-extension rooveterinaryinc.roo-cline \
    && code-server --install-extension GitHub.vscode-pull-request-github

USER root

# Copy startup script
COPY docker/azure_start.sh /usr/local/bin/azure_start.sh
RUN chmod +x /usr/local/bin/azure_start.sh

EXPOSE 8080

# Environment defaults
ENV CODE_SERVER_PORT=8080 \
    WORKSPACE_DIR=/home/coder/workspace

# Entrypoint fetches secrets from Key Vault, then starts supervisor
ENTRYPOINT ["/usr/local/bin/azure_start.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
