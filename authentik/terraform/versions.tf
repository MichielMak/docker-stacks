terraform {
  required_version = ">= 1.10"

  required_providers {
    authentik = {
      source  = "goauthentik/authentik"
      version = "2026.8.2"
    }
  }

  # State lives in Cloudflare R2, next to cloudflare/. The endpoint holds the account ID, so CI passes it with -backend-config.
  backend "s3" {
    bucket       = "tofu-state"
    key          = "authentik/terraform.tfstate"
    region       = "auto"
    use_lockfile = true

    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
    use_path_style              = true
  }
}

# Authenticates with the AUTHENTIK_URL and AUTHENTIK_TOKEN environment variables.
provider "authentik" {}
