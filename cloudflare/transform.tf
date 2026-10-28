locals {
  # Proxy headers a visitor could send to fake their IP or host. Traefik trusts these headers
  # on its tunnel entrypoint, so they must only ever come from Cloudflare itself.
  # Cloudflare re-adds X-Forwarded-For (with only the visitor's IP) and X-Forwarded-Proto after this phase.
  # Lowercase: Cloudflare stores header names that way, so plans stay clean.
  stripped_request_headers = [
    "x-forwarded-for",
    "x-real-ip",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-forwarded-server",
    "x-forwarded-prefix",
    "x-forwarded-uri",
    "x-forwarded-method",
    "forwarded",
  ]
}

# The zone's Request Header Transform Rules. This replaces the whole list: rules added in
# the dashboard are removed on the next apply.
resource "cloudflare_ruleset" "request_headers" {
  zone_id = var.zone_id
  name    = "default"
  kind    = "zone"
  phase   = "http_request_late_transform"

  rules = [
    {
      description = "Strip visitor-supplied proxy headers"
      expression  = "true"
      action      = "rewrite"
      action_parameters = {
        headers = { for h in local.stripped_request_headers : h => { operation = "remove" } }
      }
    },
  ]
}
