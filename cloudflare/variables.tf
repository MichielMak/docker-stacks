variable "domain" {
  type    = string
  default = "makstra.nl"
}

variable "account_id" {
  type = string
}

variable "zone_id" {
  type = string
}

variable "tunnel_id" {
  type = string
}

variable "access_guest_emails" {
  description = "Email addresses outside the Authentik users group that may log in to apps with guests = true. JSON list, from the ACCESS_GUEST_EMAILS secret."
  type        = list(string)
  sensitive   = true
}

variable "github_token" {
  description = "GitHub token for reading api.github.com/meta. Optional: without it the request is anonymous and limited per IP address. CI passes the workflow's GITHUB_TOKEN."
  type        = string
  default     = ""
  sensitive   = true
}
