# One-time import of what was built in the admin UI. After the first apply these are no-ops.

import {
  for_each = toset(keys(local.apps))
  to       = authentik_application.app[each.key]
  id       = each.key
}

import {
  for_each = {
    cloudflare       = 2
    nextcloud        = 3
    audiobookshelf   = 4
    immich           = 10
    mealie           = 12
    romm             = 15
    tandoor          = 17
    booklore         = 19
    komodo           = 24
    owncloud-spa     = 25
    owncloud-desktop = 26
    owncloud-ios     = 27
    home-assistant   = 31
    termix           = 33
    bambuddy         = 35
    gotify           = 37
  }
  to = authentik_provider_oauth2.app[each.key]
  id = each.value
}

import {
  to = authentik_provider_proxy.forwardauth
  id = "22"
}

import {
  to = authentik_provider_proxy.booklore_guest
  id = "29"
}

import {
  to = authentik_outpost.embedded
  id = "a9a9d937-1e37-4a90-bdfc-2d286f0dc7be"
}

import {
  for_each = {
    admin    = "ea68e839-a95a-44fc-8382-8cf3079d6b88"
    internal = "2ff182bd-ecbb-44ab-ac25-7913045d9344"
    user     = "ac5a35ec-2250-403e-b570-5ad1e0620810"
  }
  to = authentik_group.custom[each.key]
  id = each.value
}

import {
  for_each = {
    bambuddy       = "f3823f3c-a7b5-40c4-bb63-58a74a473832"
    gotify         = "20864ffd-0668-4cbd-a67e-6dddf4c48c0b"
    home-assistant = "61ce608d-d8d0-4227-b053-7e516743293a"
    immich         = "99fafa81-60f5-453e-b6bb-55fb33ae39e7"
    komodo         = "f8fb9b8e-c631-41bf-8b67-ffcba1a1414f"
    nextcloud      = "1773304d-2128-460c-b0b9-c9c3e3ad7972"
    npm            = "58555813-829f-42d6-8488-4566d10efd18"
    tandoor        = "a0fe0c51-f0b9-4312-88f6-0c06803cbeeb"
    termix         = "75884729-e8d1-4202-b2e8-bb5408afcc8b"
  }
  to = authentik_policy_binding.internal_only[each.key]
  id = each.value
}
