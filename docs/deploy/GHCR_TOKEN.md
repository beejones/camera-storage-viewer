# GHCR_TOKEN (GitHub Container Registry)

`GHCR_TOKEN` is a GitHub Personal Access Token used to authenticate to **GitHub Container Registry** (`ghcr.io`).

You need it when:
- your image is **private** and Azure Container Instances (ACI) must **pull** it, or
- you run scripts that **build + push** images to `ghcr.io` from your machine.

## Create a token

Recommended (simplest): **classic** PAT.

In GitHub: **Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token**.

Minimum scopes:
- `read:packages` (pull private images)
- `write:packages` (push images)

If the package is associated with a **private** repository, you may also need:
- `repo`

Notes:
- If your org enforces SSO, you must authorize the token for SSO.
- Treat this token like a password.

## Where to put it

Local deploy (ACI via `scripts/deploy/azure_deploy_container.py`): set these in `.env.deploy`:

- `GHCR_PRIVATE=true`
- `GHCR_USERNAME=<your-github-username-or-org>`
- `GHCR_TOKEN=<your-pat>`

GitHub Actions deploy:
- store `GHCR_TOKEN` as a repository **Secret**
- store `GHCR_USERNAME` as a repository **Variable**

If you use `python3 scripts/deploy/gh_sync_actions_env.py --set`, it will sync these from `.env.deploy`.

Note: `python3 scripts/deploy/azure_deploy_container.py` (run locally) syncs GitHub Actions vars/secrets by default; use `--no-set-vars-secrets` to disable (CI does this).

## GitHub Actions: Package Permissions

When pushing from GitHub Actions using `GITHUB_TOKEN`, you may see:
```
denied: permission_denied: write_package
```

**Fix 1: Link Package to Repository**

If the package already exists:
1. Go to **your GitHub profile** → **Packages** → find your package
2. Click **Package settings** (gear icon)
3. Under **"Manage Actions access"**, click **Add Repository**
4. Add your repository with **Write** access

**Fix 2: First-time push (new package)**

For a brand new package, the first push must come from a user with a PAT (not `GITHUB_TOKEN`). After the first push, link the package to the repo as above.

**Fix 3: Use a Personal Access Token in CI**

If the above doesn't work:
1. Create a classic PAT with `write:packages` scope
2. Add it as a repository secret (e.g., `GHCR_PAT`)
3. Update the workflow to use this PAT instead of `GITHUB_TOKEN`

## Quick verification

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
docker pull ghcr.io/<owner>/camera-storage-viewer:latest
```

If you accidentally paste a token into chat/logs, revoke it immediately and generate a new one.
