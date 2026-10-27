# GitHub's webhook source ranges. Read on every plan, so the allow rules below pick up
# new ranges by themselves (the daily scheduled apply in cloudflare.yml makes it live).
data "http" "github_meta" {
  url             = "https://api.github.com/meta"
  request_headers = { Accept = "application/vnd.github+json" }

  lifecycle {
    postcondition {
      condition     = length(try(jsondecode(self.response_body).hooks, [])) > 0
      error_message = "api.github.com/meta returned no webhook ranges."
    }
  }
}

locals {
  github_hook_ips = join(" ", jsondecode(data.http.github_meta.response_body).hooks)

  blocked_countries = ["CN", "UA", "RU", "IN", "IR"]
}

# The zone's custom WAF rules. This replaces the whole list: rules added in the dashboard are
# removed on the next apply. Order matters: the skip rules must come before the block rules.
resource "cloudflare_ruleset" "custom" {
  zone_id = var.zone_id
  name    = "default"
  kind    = "zone"
  phase   = "http_request_firewall_custom"

  rules = [
    {
      description       = "Allow GitHub webhooks to Hub"
      expression        = "(http.host eq \"hub.${var.domain}\" and http.request.uri.path eq \"/webhook\" and http.request.method eq \"POST\" and ip.src in {${local.github_hook_ips}})"
      action            = "skip"
      action_parameters = { ruleset = "current" }
      logging           = { enabled = true }
    },
    {
      description       = "Allow GitHub webhooks to Komodo"
      expression        = "(http.host eq \"komodo-webhook.${var.domain}\" and ip.src in {${local.github_hook_ips}})"
      action            = "skip"
      action_parameters = { ruleset = "current" }
      logging           = { enabled = true }
    },
    {
      description       = "Allow HetrixTools"
      expression        = "(ip.src in $hetrixtools)"
      action            = "skip"
      action_parameters = { ruleset = "current" }
      logging           = { enabled = true }
    },
    {
      description = "Block bots"
      expression  = "(cf.client.bot)"
      action      = "block"
    },
    {
      description = "Block countries"
      expression  = join(" or ", [for c in local.blocked_countries : "(ip.geoip.country eq \"${c}\")"])
      action      = "block"
    },
  ]
}
