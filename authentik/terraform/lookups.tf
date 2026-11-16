# Built-in objects the providers point at. Looked up by name, so the code doesn't carry their UUIDs.

data "authentik_flow" "explicit_consent" {
  slug = "default-provider-authorization-explicit-consent"
}

data "authentik_flow" "implicit_consent" {
  slug = "default-provider-authorization-implicit-consent"
}

data "authentik_flow" "provider_invalidation" {
  slug = "default-provider-invalidation-flow"
}

data "authentik_flow" "default_authentication" {
  slug = "default-authentication-flow"
}

data "authentik_certificate_key_pair" "self_signed" {
  name = "authentik Self-signed Certificate"

  # Only the ID is needed: keep the key and certificate out of the state file.
  fetch_key         = false
  fetch_certificate = false
}

# OAuth2 scope mappings, keyed by the short name used in oauth2.tf.
data "authentik_property_mapping_provider_scope" "builtin" {
  for_each = toset(["openid", "profile", "email", "offline_access"])
  managed  = "goauthentik.io/providers/oauth2/scope-${each.key}"
}

data "authentik_property_mapping_provider_scope" "custom" {
  for_each = {
    preferred_username = "Preferred username"
    email_verified     = "email_verified"
    owncloud_role      = "owncloud-role"
  }
  name = each.value
}

locals {
  scopes = merge(
    { for k, v in data.authentik_property_mapping_provider_scope.builtin : k => v.id },
    { for k, v in data.authentik_property_mapping_provider_scope.custom : k => v.id },
  )

  # The Google login source (Directory > Federation and Social login). Providers with
  # federate_google = true accept Google-issued tokens. Not managed here yet: it holds a client secret.
  google_source_id = "e9b20ca7-7910-4dc0-aa04-86b8e8304b97"
}
