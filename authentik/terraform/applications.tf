# Applications, keyed by slug. The provider is the entry with the same key in oauth2.tf,
# or proxy = "<resource name>" for a proxy provider in proxy.tf.
#
# internal_only = true: only members of the internal group may open it. Without it, every user can.

locals {
  apps = {
    audiobookshelf = { name = "audiobookshelf", icon = "audiobookshelf.svg", url = "https://audiobookshelf.makstra.nl" }
    bambuddy       = { name = "bambuddy", icon = "bambuddy.png", url = "https://bambuddy.makstra.nl", internal_only = true }
    booklore       = { name = "grimmory", icon = "grimmory.svg", url = "https://grimmory.makstra.nl" }
    booklore-guest = { name = "booklore-guest", icon = "", url = "https://bookloretest.makstra.nl", proxy = "booklore_guest" }
    cloudflare     = { name = "cloudflare", icon = "cloudflare.svg", url = "" }
    gotify         = { name = "gotify", icon = "gotify.svg", url = "https://notifications.makstra.nl", internal_only = true, binding_order = 1 }
    home-assistant = { name = "Home Assistant", icon = "hass.svg", url = "https://hass.makstra.nl", internal_only = true }
    immich         = { name = "immich", icon = "immich.png", url = "https://photos.makstra.nl", internal_only = true }
    komodo         = { name = "komodo", icon = "komodo.svg", url = "https://komodo.makstra.nl", internal_only = true }
    mealie         = { name = "mealie", icon = "mealie.svg", url = "https://mealie.makstra.nl" }
    nextcloud      = { name = "nextcloud", icon = "nextcloud.svg", url = "https://nextcloud.makstra.nl", internal_only = true }
    npm            = { name = "npm", icon = "", url = "", proxy = "forwardauth", internal_only = true }
    romm           = { name = "romm", icon = "romm.svg", url = "https://romm.makstra.nl" }
    tandoor        = { name = "tandoor", icon = "tandoor.svg", url = "https://tandoor.makstra.nl/", internal_only = true }
    termix         = { name = "termix", icon = "termix.svg", url = "https://termix.makstra.nl", internal_only = true }
  }

  proxy_provider_ids = {
    forwardauth    = authentik_provider_proxy.forwardauth.id
    booklore_guest = authentik_provider_proxy.booklore_guest.id
  }
}

resource "authentik_application" "app" {
  for_each = local.apps

  slug              = each.key
  name              = each.value.name
  protocol_provider = can(each.value.proxy) ? local.proxy_provider_ids[each.value.proxy] : authentik_provider_oauth2.app[each.key].id
  meta_icon         = each.value.icon
  meta_launch_url   = each.value.url

  backchannel_providers = []
  group                 = ""
  meta_description      = ""
  meta_publisher        = ""
  meta_hide             = false
  open_in_new_tab       = false
  policy_engine_mode    = "any"
}

resource "authentik_policy_binding" "internal_only" {
  for_each = { for k, v in local.apps : k => v if try(v.internal_only, false) }

  target  = authentik_application.app[each.key].uuid
  group   = authentik_group.custom["internal"].id
  order   = try(each.value.binding_order, 0)
  timeout = 30
}
