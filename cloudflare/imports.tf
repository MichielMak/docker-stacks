# One-time import of what was built in the dashboard. After the first apply these are no-ops.

import {
  to = cloudflare_zero_trust_tunnel_cloudflared_config.home
  id = "${var.account_id}/${var.tunnel_id}"
}
