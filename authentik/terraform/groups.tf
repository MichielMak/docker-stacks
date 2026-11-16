# Custom groups. The built-in "authentik ..." groups belong to Authentik and stay out of this.
# Membership is managed in the admin UI: changes to users are ignored here.

locals {
  groups = {
    admin    = { superuser = true }
    internal = { superuser = false }
    user     = { superuser = false }
  }
}

resource "authentik_group" "custom" {
  for_each = local.groups

  name         = each.key
  is_superuser = each.value.superuser
  attributes   = jsonencode({})
  roles        = []

  lifecycle {
    # Authentik reports internal as its own parent. Ignored until that is cleaned up in the UI.
    ignore_changes = [users, parents]
  }
}
