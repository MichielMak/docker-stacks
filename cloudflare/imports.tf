# One-time import of what was built in the dashboard. After the first apply these are no-ops.

import {
  to = cloudflare_zero_trust_tunnel_cloudflared_config.home
  id = "${var.account_id}/${var.tunnel_id}"
}

import {
  for_each = {
    arl            = "212d80415015413796e602f53621322f"
    audiobookshelf = "ddf77eae982e7ac819b257c03388ec36"
    auth           = "fe03a236d6a00b1373f7f1de526eedf5"
    books          = "69dacffd1134e1f73309860900056661"
    deemix         = "9c359755c1abc4abfb26d1282cb1450a"
    hub            = "a2cd5a2d178f8bcdec25db3af253f3f2"
    komodo-webhook = "7beb082acb8f88f6166431d890dc327b"
    mealie         = "9bc7d17fd732f379a00386683ffbb618"
    overseerr      = "6d4cf41aee02c7967fc67db63601f048"
    plex           = "55d45c489257bfe0a633e46bdb6e9322"
    romm           = "8d369281a85cb610482d39c0f54973b0"
    spotizerr      = "54dce804720d04104b064446942c9b9f"
    wizarr         = "38c3991a26a8c981cd098238d21b9345"
  }
  to = cloudflare_dns_record.tunnel[each.key]
  id = "${var.zone_id}/${each.value}"
}

import {
  to = cloudflare_zero_trust_access_policy.allow_users
  id = "${var.account_id}/095a6883-910c-47f3-8700-0d36f31456aa"
}

import {
  for_each = {
    arl       = "050d9db9-5780-4957-a83b-448caf523d28"
    spotizerr = "0cf3767b-481e-4880-bdd5-c9c0c28c6587"
    deemix    = "2bab9892-f006-4d5c-93f4-aa010c15e21a"
  }
  to = cloudflare_zero_trust_access_application.app[each.key]
  id = "accounts/${var.account_id}/${each.value}"
}

# The custom rules were built in the dashboard. Its ruleset ID is looked up, not hardcoded.
data "cloudflare_rulesets" "zone" {
  zone_id = var.zone_id
}

import {
  to = cloudflare_ruleset.custom
  id = "zones/${var.zone_id}/${one([for r in data.cloudflare_rulesets.zone.rulesets : r.id if r.phase == "http_request_firewall_custom" && r.kind == "zone"])}"
}
