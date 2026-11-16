# Proxy providers: Traefik's authentik-forwardauth middleware asks the embedded outpost whether a request may pass.

resource "authentik_provider_proxy" "forwardauth" {
  # forward_domain: one login for every host under cookie_domain. This is what authentik-forwardauth@docker uses.
  name                  = "Provider for npm"
  mode                  = "forward_domain"
  external_host         = "https://auth.makstra.nl"
  cookie_domain         = "makstra.nl"
  authorization_flow    = data.authentik_flow.implicit_consent.id
  invalidation_flow     = data.authentik_flow.provider_invalidation.id
  access_token_validity = "hours=72"

  intercept_header_auth    = true
  jwt_federation_sources   = [local.google_source_id]
  jwt_federation_providers = []
}

resource "authentik_provider_proxy" "booklore_guest" {
  # Not attached to the outpost below, so it currently serves nothing.
  name                  = "Provider for booklore-guest"
  mode                  = "forward_single"
  external_host         = "https://bookloretest.makstra.nl"
  authorization_flow    = data.authentik_flow.implicit_consent.id
  invalidation_flow     = data.authentik_flow.provider_invalidation.id
  access_token_validity = "hours=24"

  intercept_header_auth    = true
  jwt_federation_sources   = []
  jwt_federation_providers = []
}

resource "authentik_outpost" "embedded" {
  name               = "authentik Embedded Outpost"
  type               = "proxy"
  protocol_providers = [authentik_provider_proxy.forwardauth.id]
  service_connection = ""

  config = jsonencode({
    authentik_host                   = "https://auth.makstra.nl"
    authentik_host_browser           = ""
    authentik_host_insecure          = false
    container_image                  = null
    docker_labels                    = null
    docker_map_ports                 = true
    docker_network                   = "authentik_default"
    kubernetes_disabled_components   = []
    kubernetes_httproute_annotations = {}
    kubernetes_httproute_parent_refs = []
    kubernetes_image_pull_secrets    = []
    kubernetes_ingress_annotations   = {}
    kubernetes_ingress_class_name    = null
    kubernetes_ingress_secret_name   = "authentik-outpost-tls"
    kubernetes_json_patches          = null
    kubernetes_namespace             = "default"
    kubernetes_replicas              = 1
    kubernetes_service_type          = "ClusterIP"
    log_level                        = "info"
    object_naming_template           = "ak-outpost-%(name)s"
    refresh_interval                 = "minutes=5"
  })
}
