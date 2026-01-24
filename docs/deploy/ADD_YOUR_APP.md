# Add your own app to the container

This repository is currently focused on a lightweight **FTP-only** container (camera uploads) deployed to **Azure Container Instances**.

The older docs in this folder were written for a different model (code-server behind Caddy + Basic Auth) and are no longer applicable.

If you’re adding a future viewer/API, the recommended path is:

1. Extend the existing Python app under `src/` (or add a separate service).
2. Update [docker/Dockerfile](../../docker/Dockerfile) to run the additional process.
3. If you need multiple containers, extend the container group YAML generator in [scripts/deploy/azure_deploy_yaml_helpers.py](../../scripts/deploy/azure_deploy_yaml_helpers.py).

For current deployment instructions, see [docs/deploy/AZURE_CONTAINER.md](AZURE_CONTAINER.md).
