# CLAUDE.md

## Project Overview

This is a **GitOps-based Docker orchestration repository** managing 60+ self-hosted services (home lab) using:
- **Komodo** — declarative Docker stack manager that continuously syncs this repo to the Docker environment
- **Traefik** — reverse proxy configured entirely via Docker container labels
- **Renovate** — automated container image updates via PRs, run self-hosted from `.github/workflows/renovate.yml`

## Repository Structure

Each service lives in its own directory with a `compose.yaml`:
```
/<stack-name>/
  compose.yaml        # Docker Compose configuration
  secrets.sops.env    # SOPS-encrypted secrets (only if the stack has secrets)
```

Notable directories:
- `traefik/` — reverse proxy; `traefik/dynamic/` holds static route configs for non-Docker services (HASS, Unifi, Proxmox, TrueNAS)
- `komodo/` — the GitOps orchestrator itself (with MongoDB backend)
- `komodo/resources/` — everything Komodo manages, as Resource Sync TOML (stacks, server, repos, procedures, alerter, builder, plain Variables, the sync itself)
- `scripts/` — `komodo-migrate.py`, the one-off migration tool that moved stack secrets into SOPS and generated the TOML
- `homepage/` — dashboard aggregating all services
- `authentik/` — OIDC provider for SSO/ForwardAuth

## Compose File Conventions

### Image pinning
All images use SHA256 digests for reproducibility:
```yaml
image: linuxserver/plex:1.43.1@sha256:<digest>
```

### Environment variables
Use `${VARIABLE}` placeholders in `compose.yaml`. Never hardcode values there. They're filled in at deploy time from two places:
- **Plain values:** the stack's `environment` in `komodo/resources/stacks.toml`. Shared values are `[[VAR]]` references to Komodo Variables (`komodo/resources/variables.toml`).
- **Secrets:** `<stack>/secrets.sops.env`, decrypted by the stack's `sops exec-env` wrapper. See "Secrets (SOPS)" below.

Komodo Variables (plain): `DOMAIN`, `PUID`, `PGID`, `DOCKER_DATA_DIR`, `DOCKER_STACKS_DIR`, `MEDIA_DIR`, `OVERIG_DIR`, `PRESTAGE_DIR`. `TZ` comes from periphery's own environment unless a stack sets it.

### Network
All services that need Traefik exposure join the external `npm` network:
```yaml
networks:
  npm:
    external: true
```

### Traefik labels (required for HTTPS exposure)
```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.<name>.rule=Host(`<name>.${DOMAIN}`)
  - traefik.http.routers.<name>.entrypoints=websecure
  - traefik.http.routers.<name>.tls=true
  - traefik.http.services.<name>.loadbalancer.server.port=<port>
```
If a container has multiple networks, also add:
```yaml
  - traefik.docker.network=npm
```

### Homepage dashboard labels
```yaml
labels:
  - homepage.group=<Group>
  - homepage.name=<Display Name>
  - homepage.icon=<name>.png
  - homepage.href=https://<name>.${DOMAIN}/
  - homepage.description=<short description>
  # Optional widget:
  - homepage.widget.type=<type>
  - homepage.widget.url=http://<service>:<port>
  - homepage.widget.key=${SERVICE_API_KEY}
```

## Renovate Rules

`renovate.json` enforces strict versioning:
- **Rejects**: prerelease tags (alpha, beta, rc, nightly), floating tags (`latest`, `stable`, hash-only)
- **Groups**: updates per stack directory (one PR per stack)
- **Automerge**: enabled; max 6 PRs/hour, 10 concurrent
- Custom version extraction for Hotio, BamBuddy, Lidarr, PostgreSQL, Immich images

When adding a new service, Renovate will pick up the image automatically. If the image uses non-standard versioning, add a custom `packageRule` in `renovate.json`.

Renovate runs hourly as a GitHub Actions workflow (`.github/workflows/renovate.yml`). It manages this repository and `MichielMak/open_agb_firm` — add further repositories to the `renovate_repos` and `renovate_repositories` lists at the top of the workflow. It authenticates as a GitHub App using the `RENOVATE_CLIENT_ID` and `RENOVATE_PRIVATE_KEY` repository secrets, and pulls Docker Hub metadata with `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`. The workflow can also be triggered manually, with options to reset or disable the repository cache.

## Secrets (SOPS)

Stack secrets live in `<stack>/secrets.sops.env`: a dotenv file, encrypted with SOPS + age. Komodo deploys these stacks through:
```toml
compose_cmd_wrapper = "sops exec-env secrets.sops.env '[[COMPOSE_COMMAND]]'"
compose_cmd_wrapper_include = ["up", "pull", "build", "run"]
```
The wrapper runs in the stack's run directory inside `komodo-periphery`. Periphery is built with the `sops` binary (`komodo/periphery.Dockerfile`).

- **Add or change a secret:** run `sops <stack>/secrets.sops.env`. It needs the age private key and opens the decrypted file in `$EDITOR`. Commit, then **deploy the stack by hand**: the Gitops procedure only redeploys when compose files change.
- **First secret for a stack:** create the file with `sops <stack>/secrets.sops.env` (`.sops.yaml` picks the key), then add the two wrapper lines above to the stack in `komodo/resources/stacks.toml`. A wrapper without a secrets file fails the deploy.
- **Values are passed literally.** Unlike a compose `.env` file, `$` isn't expanded, and quotes aren't stripped.
- **Never** put a secret in `stacks.toml`, `compose.yaml`, or a plaintext `secrets.env`. Pre-commit blocks unencrypted `*.sops.env`, `secrets.env` and `*.agekey`, and gitleaks runs on every commit.
- **Never** add `config` to `compose_cmd_wrapper_include`: Komodo 2.3.x logs the resolved compose config unredacted ([moghtech/komodo#1636](https://github.com/moghtech/komodo/issues/1636)).
- **Komodo-internal secrets** stay in Komodo as secret Variables, referenced as `[[NAME]]`, for example the alerter URL `[[KOMODO_ALERTER_GOTIFY_URL]]`. They're not in `variables.toml`.

**Rules for agents:** never decrypt, print or log a secret value. That includes `sops -d`, `printenv`, `docker inspect`, app logs, and Komodo Update logs. To check a value, compare its length or hash, never the value.

**The age key:**
- The public key is in `.sops.yaml`.
- The private key lives in three places:
  - on the host at `${DOCKER_DATA_DIR}/komodo/sops/age.key` (root, 0400, a plain-text age identity file, not RTF), mounted into periphery
  - in the password manager
  - on the Mac at `~/Library/Application Support/sops/age/keys.txt`
- After replacing the key file on the host, restart `komodo-periphery`, because a single-file bind mount keeps pointing at the old file.
- A leaked private key means rotating every secret, and re-encrypting with a new key.

## Adding a New Stack

1. Create `/<stack-name>/compose.yaml`
2. Pin the image with a SHA256 digest
3. Use `${VARIABLE}` for all environment-specific values
4. Attach to the `npm` network and add Traefik labels for HTTPS
5. Add Homepage labels if the service should appear on the dashboard
6. Add the stack to `komodo/resources/stacks.toml`:
   ```toml
   [[stack]]
   name = "<stack-name>"

   [stack.config]
   server = "Local"
   linked_repo = "docker-stacks"
   run_directory = "<stack-name>"
   environment = """
   DOMAIN=[[DOMAIN]]
   DOCKER_DATA_DIR=[[DOCKER_DATA_DIR]]
   """
   ```
7. If it has secrets, create `<stack-name>/secrets.sops.env` with `sops` and add the wrapper lines (see "Secrets (SOPS)")
8. Commit, open a PR, merge. The push to `main` triggers the Gitops procedure: it pulls the repo, runs the Resource Sync (which creates the stack) and deploys every stack whose compose files changed or that was never deployed. Changes to only `stacks.toml` or `secrets.sops.env` of an existing stack still need a manual deploy.

## Key Services Reference

| Service | Directory | Purpose |
|---------|-----------|---------|
| Traefik | `traefik/` | Reverse proxy, TLS termination |
| Komodo | `komodo/` | GitOps stack manager |
| Resource Sync | `komodo/resources/` | Komodo resources as code (TOML) |
| Authentik | `authentik/` | OIDC / SSO / ForwardAuth |
| Homepage | `homepage/` | Service dashboard |
| Renovate | `.github/workflows/renovate.yml` | Automated image updates (GitHub Actions) |
| Plex | `plex/` | Media server |
| Nextcloud | `nextcloud/` | File storage |
| Immich | `immich-app/` | Photo management |
