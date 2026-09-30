# Komodo periphery with the sops binary, so stacks can use
# compose_cmd_wrapper = "sops exec-env secrets.sops.env '[[COMPOSE_COMMAND]]'"
# Keep the periphery version in sync with komodo-core in compose.yaml.
FROM ghcr.io/getsops/sops:v3.13.3-alpine@sha256:ae501277bf742f1662e0f881f43dd8fd6798b489a8058e921dbf6cda597140ea AS sops

FROM docker.io/moghtech/komodo-periphery:2.3.3@sha256:fa3f1a641a265216066676950d5eecdf506955a7bafe2f01996135ae93115cd5
COPY --from=sops /usr/local/bin/sops /usr/local/bin/sops
