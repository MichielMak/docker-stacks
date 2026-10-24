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
