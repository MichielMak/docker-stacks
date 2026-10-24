locals {
  # Cloudflare Access login: the Authentik OIDC identity provider and the Access group of its users.
  access_idp_id         = "20d4fa7c-8b38-41e1-adcd-86ddf3901cc1"
  access_users_group_id = "f4e2a446-5af3-426c-91c5-5e5c8f811605"

  # Hosts behind Cloudflare Access, keyed by <name>.${var.domain}. Each must also be in public_hosts.
  # guests = true also lets in the addresses in var.access_guest_emails.
  access_apps = {
    arl       = { name = "arl", http_only_cookie = false, guests = false }
    spotizerr = { name = "Spotizerr", http_only_cookie = false, guests = false }
    deemix    = { name = "deemix", http_only_cookie = true, guests = true }
  }
}

resource "cloudflare_zero_trust_access_policy" "allow_users" {
  account_id       = var.account_id
  name             = "Allow users"
  decision         = "allow"
  session_duration = "24h"
  include          = [{ group = { id = local.access_users_group_id } }]
}

resource "cloudflare_zero_trust_access_policy" "guests" {
  account_id       = var.account_id
  name             = "Allow guests"
  decision         = "allow"
  session_duration = "24h"
  include          = [for email in var.access_guest_emails : { email = { email = email } }]
}

resource "cloudflare_zero_trust_access_application" "app" {
  for_each = local.access_apps

  account_id                 = var.account_id
  name                       = each.value.name
  type                       = "self_hosted"
  domain                     = "${each.key}.${var.domain}"
  destinations               = [{ type = "public", uri = "${each.key}.${var.domain}" }]
  allowed_idps               = [local.access_idp_id]
  auto_redirect_to_identity  = true
  app_launcher_visible       = true
  enable_binding_cookie      = false
  options_preflight_bypass   = false
  http_only_cookie_attribute = each.value.http_only_cookie
  session_duration           = "24h"
  tags                       = []

  policies = concat(
    [{ id = cloudflare_zero_trust_access_policy.allow_users.id, precedence = 1 }],
    each.value.guests ? [{ id = cloudflare_zero_trust_access_policy.guests.id, precedence = 2 }] : [],
  )
}
