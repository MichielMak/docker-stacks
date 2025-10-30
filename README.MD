# Docker Stacks Public Repository

This repository demonstrates a GitOps workflow for managing Docker stacks using Komodo.

## 🚀 GitOps Workflow with Komodo

We use Komodo to declaratively manage Docker stacks via GitOps. All stack configurations are stored as code in this repository. Komodo continuously synchronizes the desired state defined here with your Docker environment, ensuring consistency and easy rollbacks.

## ⚙️ Environment Variables

Environment variables are managed globally and per-stack:

- **Global env files:** Shared environment variables applicable to all stacks are defined in global env files.
- **Per-stack env files:** Each stack can have its own env files to override or extend global settings.

This separation allows flexible and maintainable configuration management.

## 🌐 Traefik Reverse Proxy

All services are exposed through [Traefik](https://traefik.io/), a modern reverse proxy configured entirely via Docker container labels. This approach eliminates the need for separate configuration files and enables automatic service discovery.

### Key Features

- **Automatic HTTPS:** Wildcard SSL certificates via Let's Encrypt (DNS-01 challenge with Cloudflare)
- **Label-based routing:** Services define their routing rules directly in `compose.yaml` files
- **HTTP to HTTPS redirect:** All HTTP traffic is automatically redirected to HTTPS
- **Security headers:** Pre-configured middleware for HSTS, frame options, and other security headers
- **Dynamic configuration:** Services are automatically discovered when containers start

### Configuration Example

Services expose themselves to Traefik using labels in their `docker-compose.yaml` files:

```yaml path=null start=null
services:
  myservice:
    image: myapp:latest
    networks:
      - npm  # External network shared with Traefik
    labels:
      - traefik.enable=true
      - traefik.http.routers.myservice.rule=Host(`myservice.${DOMAIN}`)
      - traefik.http.routers.myservice.entrypoints=websecure
      - traefik.http.routers.myservice.tls=true
      - traefik.http.services.myservice.loadbalancer.server.port=8080
```

### Essential Labels

| Label | Purpose | Example |
|-------|---------|--------|
| `traefik.enable` | Enable Traefik for this container | `true` |
| `traefik.http.routers.<name>.rule` | Routing rule (typically Host) | `Host(\`app.${DOMAIN}\`)` |
| `traefik.http.routers.<name>.entrypoints` | Entry point to use | `websecure` (HTTPS) |
| `traefik.http.routers.<name>.tls` | Enable TLS | `true` |
| `traefik.http.services.<name>.loadbalancer.server.port` | Container port | `8080` |
| `traefik.docker.network` | Network to use (if multiple) | `npm` |

### Advanced: Custom Middleware

You can apply custom headers or authentication middleware:

```yaml path=null start=null
labels:
  - traefik.enable=true
  - traefik.http.routers.myservice.rule=Host(`myservice.${DOMAIN}`)
  - traefik.http.routers.myservice.entrypoints=websecure
  - traefik.http.routers.myservice.tls=true
  - traefik.http.services.myservice.loadbalancer.server.port=8080
  # Custom middleware
  - traefik.http.middlewares.myservice-headers.headers.customRequestHeaders.X-Forwarded-Proto=https
  - traefik.http.middlewares.myservice-headers.headers.customRequestHeaders.X-Forwarded-Host={host}
  - traefik.http.routers.myservice.middlewares=myservice-headers
```

### Network Configuration

All services must be on the same Docker network as Traefik (typically `npm`):

```yaml path=null start=null
networks:
  npm:
    external: true
```

## 🏠 Homepage Labels

Labels are set directly in the `docker-compose` files to integrate with the [Homepage](https://gethomepage.dev/) dashboard. These labels configure service names, icons, grouping, and dashboard links, providing a user‑friendly way to access services through the Homepage dashboard.

### Homepage Label Example

```yaml path=null start=null
labels:
  - homepage.group=Media
  - homepage.name=MyService
  - homepage.icon=myservice.png
  - homepage.href=https://myservice.${DOMAIN}/
  - homepage.description=My awesome service
  - homepage.widget.type=myservice
  - homepage.widget.url=http://myservice:8080
  - homepage.widget.key=${MYSERVICE_API_KEY}
```

## 🔄 Renovate Versioning

We use Renovate to automate dependency updates. Renovate is configured to handle versioning carefully, ensuring that updates are safe and compatible with existing stacks.

## 📂 Repository Structure and Principles

- 📝 **Declarative:** All stacks and configurations are defined declaratively
- 📦 **Modular:** Stacks are organized into separate directories with clear boundaries
- ♻️ **Reproducible:** The repository contains everything needed to reproduce the Docker environment
- 🤖 **Automated:** CI/CD pipelines and tools like Komodo and Renovate automate deployment and maintenance
- 🏷️ **Label-driven:** Traefik routing and Homepage integration configured via Docker labels

This structure promotes collaboration, transparency, and reliability in managing Docker stacks.
