locals {
  # Published through the tunnel as <name>.${var.domain}, all via Traefik.
  # To publish a service: add its name here. Traefik routes it by its Host() label.
  public_hosts = [
    "auth",
    "overseerr",
    "books",
    "deemix",
    "plex",
    "romm",
    "wizarr",
    "mealie",
    "audiobookshelf",
    "spotizerr",
    "arl",
  ]

  # Webhook endpoints: only the given path is reachable from the internet.
  webhook_routes = {
    komodo-webhook = { path = "^/listener/.*$", service = "http://core:30160" }
    hub            = { path = "^/webhook$", service = "https://traefik:443" }
  }
}

# Tunnel routes ("Published applications"). This replaces the whole list:
# edits made in the dashboard are reverted on the next apply.
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "home" {
  account_id = var.account_id
  tunnel_id  = var.tunnel_id

  config = {
    ingress = concat(
      [for name in local.public_hosts : {
        hostname = "${name}.${var.domain}"
        service  = "https://traefik:443"
        origin_request = {
          # Send the real hostname as SNI so Traefik picks the right router and cert.
          origin_server_name = "${name}.${var.domain}"
          no_tls_verify      = true
        }
      }],
      [for name, route in local.webhook_routes : {
        hostname = "${name}.${var.domain}"
        path     = route.path
        service  = route.service
        origin_request = startswith(route.service, "https://") ? {
          origin_server_name = "${name}.${var.domain}"
          no_tls_verify      = true
        } : null
      }],
      [{ service = "http_status:404" }],
    )
  }
}

resource "cloudflare_dns_record" "tunnel" {
  for_each = toset(concat(local.public_hosts, keys(local.webhook_routes)))

  zone_id = var.zone_id
  name    = "${each.key}.${var.domain}"
  type    = "CNAME"
  content = "${var.tunnel_id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}
