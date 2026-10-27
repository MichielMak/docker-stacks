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
- `cloudflare/` — OpenTofu config for the Cloudflare tunnel routes, DNS and Access (see "Cloudflare (OpenTofu)")
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
   tags = ["gitops"]  # required: the Resource Sync only manages resources with this tag

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
8. Commit, open a PR, merge. The push to `main` triggers the Gitops procedure (it also runs every 15 minutes, in case a webhook delivery fails): it pulls the repo, runs the Resource Syncs (which create the stack) and deploys every stack whose compose files changed or that was never deployed. Changes to only `stacks.toml` or `secrets.sops.env` of an existing stack still need a manual deploy.

To remove a stack, delete it from `stacks.toml`: the sync deletes it in Komodo (destroying its containers). Resources without the `gitops` tag, like the `komodo` stack and stacks whose files live only on the host, are never touched.

**Changing the Gitops procedure itself** (in `komodo/resources/procedures.toml`) breaks it until you act by hand. The procedure runs the `docker-stacks` sync, and Komodo won't update a procedure while it's running, so the sync stage fails with `procedure sync loop exited after max iterations` and no stacks deploy. After merging, run Syncs → `docker-stacks` → Execute in the Komodo UI, then check that Gitops runs green.

## Cloudflare (OpenTofu)

`cloudflare/` manages the Cloudflare Tunnel routes ("Published applications"), their DNS records and Access with OpenTofu. Don't change these in the dashboard: the next apply reverts it.

- **Publish a service:** add its name to `public_hosts` in `cloudflare/tunnel.tf`. The tunnel sends `<name>.${DOMAIN}` to `https://traefik:443`, and Traefik routes it by its `Host()` label. Put an Access app in front unless the service has its own login.
- **Put Cloudflare Access in front:** add it to `access_apps` in `cloudflare/access.tf`. Members of the Authentik users group get in; with `guests = true`, so do the addresses in the `ACCESS_GUEST_EMAILS` secret (a JSON list).
- **Webhook-only exposure:** add it to `webhook_routes` with a path regex, so only that path is public.
- **WAF custom rules** live in `cloudflare/waf.tf`. The GitHub webhook allow rules read GitHub's hook IP ranges from `api.github.com/meta` on every plan.
- `.github/workflows/cloudflare.yml` plans on pull requests and applies on merge to `main`, and daily at 05:23 UTC so new GitHub ranges go live. State lives in the R2 bucket `tofu-state`.
- Credentials are GitHub repository secrets: `CLOUDFLARE_API_TOKEN`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `ACCESS_GUEST_EMAILS`. Account, zone and tunnel IDs are repository variables (`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID`, `CLOUDFLARE_TUNNEL_ID`).
- The repo and its Actions logs are public. Never commit email addresses or other personal data: pass them in through a secret and a `sensitive = true` variable.
- Only the tunnel CNAMEs are managed. Other DNS records (mail, SPF, DKIM) and the WARP login app are still dashboard-only.
- The `cloudflared` container itself stays a remotely-managed tunnel (token in `cloudflared/secrets.sops.env`), deployed by Komodo.
- Renovate doesn't automerge the provider or OpenTofu: merging applies to the live config.

## Pushing from Claude sessions (Paseo)

Claude sessions in Paseo commit and push as the GitHub App `michiel-claude[bot]` (see `paseo/claude-github-app/README.md`). There, `git push` goes through `git-push-verified`, which recreates the commits through the GitHub API so they show as Verified. Their hashes change: the local branch is moved to the new commits. To make existing unsigned commits on a branch Verified, run `git push-verified --resign`. Tags, `--delete` and other refspecs still use a plain, unsigned push.

## Key Services Reference

| Service | Directory | Purpose |
|---------|-----------|---------|
| Traefik | `traefik/` | Reverse proxy, TLS termination |
| Komodo | `komodo/` | GitOps stack manager |
| Resource Sync | `komodo/resources/` | Komodo resources as code (TOML) |
| Authentik | `authentik/` | OIDC / SSO / ForwardAuth |
| Homepage | `homepage/` | Service dashboard |
| Renovate | `.github/workflows/renovate.yml` | Automated image updates (GitHub Actions) |
| Cloudflare | `cloudflare/` | Tunnel routes, DNS, Access (OpenTofu) |
| Paseo Hub | `hub/` | Runs Paseo agents from GitHub events and schedules (triggers in `.paseo/triggers/`) |
| Plex | `plex/` | Media server |
| Nextcloud | `nextcloud/` | File storage |
| Immich | `immich-app/` | Photo management |
