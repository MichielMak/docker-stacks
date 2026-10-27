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
# ref keeps each rule's dashboard ID, so applies update rules instead of recreating them.
resource "cloudflare_ruleset" "custom" {
  zone_id = var.zone_id
  name    = "default"
  kind    = "zone"
  phase   = "http_request_firewall_custom"

  rules = [
    {
      ref         = "3b947c2808524caebe2c5f2fda1cbb43"
      description = "Allow GitHub webhooks to Hub"
      expression  = "(http.host eq \"hub.${var.domain}\" and http.request.uri.path eq \"/webhook\" and http.request.method eq \"POST\" and ip.src in {${local.github_hook_ips}})"
      action      = "skip"
      action_parameters = {
        ruleset = "current"
        phases  = ["http_ratelimit", "http_request_firewall_managed"]
      }
      logging = { enabled = true }
    },
    {
      ref         = "b9bdc8b6db514bb2a0573b2c822d3db8"
      description = "Allow github"
      expression  = "(http.host eq \"komodo-webhook.${var.domain}\" and ip.src in {${local.github_hook_ips}})"
      action      = "skip"
      action_parameters = {
        ruleset = "current"
        phases  = ["http_ratelimit", "http_request_firewall_managed", "http_request_sbfm"]
      }
      logging = { enabled = true }
    },
    {
      ref               = "caaafa13294f4629ac7b534438d3f81b"
      description       = "allow hetrixtools"
      expression        = "(ip.src in $hetrixtools)"
      action            = "skip"
      action_parameters = { ruleset = "current" }
      logging           = { enabled = true }
    },
    {
      ref         = "bc4ac4a9b8544348a61a000e8a09eff2"
      description = "Block bots"
      expression  = "(cf.client.bot)"
      action      = "block"
    },
    {
      ref         = "797f47a987314da2a9d8fee236a61caa"
      description = "Block China, Ukraine, Russia, India and Iran"
      expression  = join(" or ", [for c in local.blocked_countries : "(ip.geoip.country eq \"${c}\")"])
      action      = "block"
    },
  ]
}
