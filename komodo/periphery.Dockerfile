# Komodo periphery with the sops binary, so stacks can use
# compose_cmd_wrapper = "sops exec-env secrets.sops.env '[[COMPOSE_COMMAND]]'"
# Keep the periphery version in sync with komodo-core in compose.yaml.
FROM ghcr.io/getsops/sops:v3.13.3-alpine@sha256:ae501277bf742f1662e0f881f43dd8fd6798b489a8058e921dbf6cda597140ea AS sops

FROM docker.io/moghtech/komodo-periphery:2.3.4@sha256:fb4b52961fcce7a31e3776930e422967b6640f653de876f59f26dea243e29beb
COPY --from=sops /usr/local/bin/sops /usr/local/bin/sops
