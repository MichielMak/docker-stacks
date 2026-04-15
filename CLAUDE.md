# CLAUDE.md

## Project Overview

This is a **GitOps-based Docker orchestration repository** managing 60+ self-hosted services (home lab) using:
- **Komodo** — declarative Docker stack manager that continuously syncs this repo to the Docker environment
- **Traefik** — reverse proxy configured entirely via Docker container labels
- **Renovate** — automated container image updates via PRs

## Repository Structure

Each service lives in its own directory with a `compose.yaml`:
```
/<stack-name>/
  compose.yaml        # Docker Compose configuration
```

Notable directories:
- `traefik/` — reverse proxy; `traefik/dynamic/` holds static route configs for non-Docker services (HASS, Unifi, Proxmox, TrueNAS)
- `komodo/` — the GitOps orchestrator itself (with MongoDB backend)
- `homepage/` — dashboard aggregating all services
- `authentik/` — OIDC provider for SSO/ForwardAuth
- `renovate/` — self-hosted Renovate bot stack

## Compose File Conventions

### Image pinning
All images use SHA256 digests for reproducibility:
```yaml
image: linuxserver/plex:1.43.1@sha256:<digest>
```

### Environment variables
Use `${VARIABLE}` placeholders — values are injected by Komodo from global and per-stack env files. Do not hardcode secrets.

Common global variables: `${DOMAIN}`, `${PUID}`, `${PGID}`, `${TZ}`, `${DOCKER_DATA_DIR}`, `${MEDIA_DIR}`

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

## Adding a New Stack

1. Create `/<stack-name>/compose.yaml`
2. Pin the image with a SHA256 digest
3. Use `${VARIABLE}` for all environment-specific values
4. Attach to the `npm` network and add Traefik labels for HTTPS
5. Add Homepage labels if the service should appear on the dashboard
6. Commit — Komodo will pick up the new stack automatically

## Key Services Reference

| Service | Directory | Purpose |
|---------|-----------|---------|
| Traefik | `traefik/` | Reverse proxy, TLS termination |
| Komodo | `komodo/` | GitOps stack manager |
| Authentik | `authentik/` | OIDC / SSO / ForwardAuth |
| Homepage | `homepage/` | Service dashboard |
| Renovate | `renovate/` | Automated image updates |
| Plex | `plex/` | Media server |
| Nextcloud | `nextcloud/` | File storage |
| Immich | `immich-app/` | Photo management |
