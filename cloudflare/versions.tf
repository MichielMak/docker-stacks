terraform {
  required_version = ">= 1.10"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.28.1"
    }
    http = {
      source  = "hashicorp/http"
      version = "3.6.3"
    }
  }

  # State lives in Cloudflare R2. The endpoint holds the account ID, so CI passes it with -backend-config.
  backend "s3" {
    bucket       = "tofu-state"
    key          = "cloudflare/terraform.tfstate"
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

# Authenticates with the CLOUDFLARE_API_TOKEN environment variable.
provider "cloudflare" {}
