# Plan: Komodo as code (Resource Sync TOML) + SOPS secrets

Status: Phases 1–2 merged, Phase 3 in PR `komodo-migrate-script`. Written 2026-09-30 to be executed in a fresh session.
Branch/worktree: `komodo-iac-sops` at `/home/paseo/workspace/docker-stacks-komodo-iac-sops`.

## Goal

Everything Komodo knows about (stacks, repos, servers, procedures, alerters, variables, the sync itself)
is declared in TOML in this **public** repo. Stack secrets live next to each stack as SOPS-encrypted
dotenv files and are injected at deploy time via Komodo's `compose_cmd_wrapper`. No `compose.yaml`
file needs to change.

## Decisions already made

| Topic | Decision |
|---|---|
| Where config lives | This public repo (`MichielMak/docker-stacks`), under `komodo/resources/` |
| Secret encryption | SOPS with age. Public key in `.sops.yaml`, private key only on the host + password manager |
| Secret file layout | One file per stack: `<stack>/secrets.sops.env` (dotenv format) |
| Injection | Per-stack `compose_cmd_wrapper = "sops exec-env secrets.sops.env '[[COMPOSE_COMMAND]]'"` in TOML |
| compose files | Unchanged. Existing `${VAR}` placeholders get their values from the wrapper's process env |
| Migration | Done by a script the **user runs**. The agent writes the script but never sees secret values |

## Hard rules for the executing agent

1. **Never read, print, or log a secret value.** That includes Komodo API responses containing
   `environment`, `docker inspect`, `printenv`, `.env` files on the host, Komodo Update logs, and
   decrypted SOPS output. If a command could output a value, don't run it; write it into the script
   for the user to run.
2. Scripts output **key names only**. To verify a value exists, check its length or hash, never the value.
3. Plaintext secrets are never written to disk in the repo. Pipe them into `sops` via stdin.
4. Never add `"config"` to `compose_cmd_wrapper_include`. Komodo 2.3.x logs the resolved compose
   config unredacted in Update logs when the wrapper applies to `config`
   ([moghtech/komodo#1636](https://github.com/moghtech/komodo/issues/1636), open as of 2026-09-30).
5. Keep Resource Sync `delete = false` until the user has reviewed a clean diff.
6. `gitleaks` (already in `.pre-commit-config.yaml`) must pass on every commit.
7. One PR per phase (see "PR breakdown"). Don't push to `main`.

## Current state (facts from the repo)

- Komodo **2.3.3** core + periphery, both in `komodo/compose.yaml`. Periphery mounts
  `${DOCKER_STACKS_DIR}` at the same path, plus the docker socket.
- `KOMODO_SYNC_DIRECTORY=/syncs` is already set on core, but there are no TOML files anywhere in the repo.
- There are 67 stack directories, and none of them use `env_file:`. All config goes through `${VAR}` interpolation.
- About 150 distinct `${VAR}` names are used. The top non-secret globals are `DOMAIN`, `DOCKER_DATA_DIR`, `PUID`,
  `PGID`, `MEDIA_DIR`, `TZ`, and `DOCKER_STACKS_DIR`. Many are secrets: `*_PASSWORD`, `*_TOKEN`, `*_API`,
  `*_KEY`, `*_SECRET`, `ARL`-style tokens, and so on.
- Pre-commit already runs gitleaks, check-yaml, renovate validator, and `docker compose config --no-interpolate`.
- The README says env comes from "global env files" plus per-stack env. Where the globals actually live
  (Komodo Variables? a host file?) is **unknown**. See Phase 0.

## Unknowns to resolve in Phase 0

1. How stacks get their files: `linked_repo` (Komodo Repo resource), a git repo per stack, or `files_on_host`
   pointing at a clone under `${DOCKER_STACKS_DIR}`? This determines `run_directory` and the wrapper's cwd.
2. Where global vars (`DOMAIN`, `PUID`, …) come from: Komodo Variables interpolated as `[[VAR]]` into each
   stack's environment, or a host-level env file?
3. Whether the `komodo` stack is itself managed by Komodo. This is a bootstrap problem, covered in Phase 6.
   **Resolved (2026-09-30):** Komodo syncs the `komodo` stack files from git, but the user runs
   `docker compose up` for it by hand. Komodo never deploys it.
4. Which other resource types exist: procedures, actions, alerters, builders, repos, and more servers.
5. Which cwd the wrapper runs in (assumed: the stack's `run_directory`). Verify during the canary.

Resolve 1, 2, and 4 with the discovery mode of the script (Phase 3), which prints structure and names only.
Resolve 3 by asking the user.

---

## Phase 1: Age key + sops inside periphery (about 45 min)

The wrapper runs inside the **periphery** container, so periphery needs the `sops` binary and the age key.

User does:
1. `age-keygen -o komodo.agekey`, then save the file in the password manager.
2. Copy it to the host at `${DOCKER_DATA_DIR}/komodo/sops/age.key` (mode 0400, owned by root: the periphery image has no `USER`, so it runs as root).
3. Give the agent the **public** key (the `age1…` line). Only that.

Agent does:
1. Add `komodo/periphery.Dockerfile`:
   ```dockerfile
   FROM ghcr.io/getsops/sops:v3.x.y-alpine@sha256:<digest> AS sops
   FROM docker.io/moghtech/komodo-periphery:2.3.3@sha256:<digest>
   COPY --from=sops /usr/local/bin/sops /usr/local/bin/sops
   ```
   Both `FROM` lines are pinned by digest so Renovate tracks them. Check the actual sops image tag and
   binary path before writing this.
2. In `komodo/compose.yaml`, give periphery a `build:` section pointing at that Dockerfile, keeping the
   `image:` name, plus:
   ```yaml
   environment:
     SOPS_AGE_KEY_FILE: /config/sops/age.key
   volumes:
     - ${DOCKER_DATA_DIR}/komodo/sops/age.key:/config/sops/age.key:ro
   ```
3. Make sure the Komodo stack for `komodo` builds on deploy (Komodo stack option `run_build`), or document
   a manual `docker compose build`.
   **Done:** the `komodo` stack is deployed by hand, so the step is `docker compose up -d --build periphery`.
   Notes from implementing it:
   - The built image is tagged `komodo-periphery-sops:local` with `pull_policy: build`. Compose cannot tag a
     build with a digest reference, so the upstream `image:` line moved into the Dockerfile.
   - sops `v3.13.3-alpine`; binary lives at `/usr/local/bin/sops` (static Go, works on the Debian-based periphery).
   - Renovate had no `dockerfile` manager enabled. Added it, plus a rule grouping `komodo/periphery.Dockerfile`
     into the `stack: komodo` PR so core and periphery bump together.
4. Verify: `docker exec komodo-periphery sops --version`, then decrypt a throwaway test file encrypted to the
   same key, checking the exit code only.

## Phase 2: Repo scaffolding (about 30 min)

1. `.sops.yaml`:
   ```yaml
   creation_rules:
     - path_regex: (^|/)secrets\.sops\.env$
       age: age1<public key>
   ```
2. `.gitignore`: add `*.agekey`, `*.key`, `.migration/`, and `secrets.env` (a plaintext safety net).
3. Pre-commit hooks (local):
   - `sops-encrypted`: every staged `*.sops.env` must contain a `sops_mac=` line, otherwise fail.
   - `no-plaintext-secrets`: fail on any staged `secrets.env` or `*.agekey`.
4. Check whether gitleaks flags SOPS files (ENC[…] values and metadata). If it does, add a `.gitleaks.toml`
   allowlist for `secrets\.sops\.env$`, and **only** that path.

## Phase 3: Migration script (about 1.5–2 h to write and test)

File: `scripts/komodo-migrate.py`. Use Python 3 stdlib only (`urllib`, `json`, `subprocess`, `re`,
`argparse`), plus the `sops` CLI on PATH. The user runs it on a machine that can reach Komodo and has
the age public key. Encrypting only needs the public key via `.sops.yaml`.

### Inputs
- Env: `KOMODO_URL`, `KOMODO_API_KEY`, `KOMODO_API_SECRET`. These are the same key and secret Homepage uses,
  or a dedicated read-only one.
- `--repo <path>` (default: the repo root)
- Mode flags: `--discover` (default), `--write-secrets`, `--write-toml`

### Komodo API
- Endpoint: `POST {KOMODO_URL}/read` with headers `X-Api-Key` / `X-Api-Secret`, body `{"type": "<Request>", "params": {...}}`.
- Requests: `ListFullStacks`, `ListVariables`, `ExportAllResourcesToToml`. **Check these names and
  response shapes against the komodo_client 2.3.x docs** (docs.rs/komodo_client) before relying on them.

### `--discover` (safe for the agent to see the output)
For each stack, print:
- the source mode (`linked_repo` / `repo` / `files_on_host`), `run_directory`, `file_paths`, and server
- env key names, each tagged `secret` / `plain` / `ref:[[VAR]]` / `unknown`
- `${VAR}` names used in its compose files but missing from its env, and the reverse (a sanity diff
  against the repo's compose files)

Also print the Komodo Variable names with their `is_secret` flag, and the count of each resource type from the export.
**Never print values.**

### Classification
A key is **secret** if any of these hold:
1. It's a Komodo Variable with `is_secret = true` (directly, or via `[[VAR]]` interpolation).
2. Its name matches `(?i)(pass|pw|secret|token|key|api|arl|auth|cookie|jwt|claim|oauth|credential)`,
   or is `ACCOUNTID`/`TUNNELID`/`ENDPOINT`-like. Review these by hand.
3. It's listed as secret in `scripts/secret-classification.yaml`, which overrides everything.

The overrides file is committed and holds **names only**:
```yaml
secret: [TUNNELID, ACCOUNTID]
plain: [AUTHENTIK_ERROR_REPORTING__ENABLED, GOTIFY_OIDC_SCOPES]
```

### `--write-secrets`
For each stack with at least one secret key:
1. Resolve `[[VAR]]` interpolations using the Komodo Variable values, in memory only.
2. Build dotenv text for the secret keys only, and escape or quote multi-line and JSON values
   (for example `SOCIALACCOUNT_PROVIDERS`). Check that the dotenv round-trips through `sops exec-env`.
3. Run `sops --encrypt --input-type dotenv --output-type dotenv --filename-override <stack>/secrets.sops.env /dev/stdin`
   with the plaintext on stdin, and write stdout to `<stack>/secrets.sops.env`.
4. Refuse to overwrite an existing file unless `--force` is passed.
5. Print `stack: N secrets written (KEY_A, KEY_B, …)`.

### `--write-toml`
1. Call `ExportAllResourcesToToml`, then parse it with `tomllib`.
2. For every `[[stack]]`: drop `environment`, then set it again to the **plain** keys only (and `[[VAR]]` refs to
   non-secret globals). If the stack has secrets, add:
   ```toml
   compose_cmd_wrapper = "sops exec-env secrets.sops.env '[[COMPOSE_COMMAND]]'"
   compose_cmd_wrapper_include = ["up", "pull", "build", "run"]
   ```
3. For other resources, redact any field that can carry a secret (alerter endpoints/URLs with tokens,
   webhook secrets, server passkeys, builder credentials) and replace it with a `[[KOMODO_…]]` reference to a
   Komodo **secret Variable**. These Komodo-internal secrets stay in Komodo's DB, not in SOPS. List what got
   redacted (names only).
4. `[[variable]]` entries: emit only non-secret variables with values. Leave secret variables out
   entirely (they stay in the DB, and sync runs with `include_variables` scoped accordingly).
5. Write to `komodo/resources/{servers,repos,stacks,procedures,alerters,variables,syncs}.toml`.
   The agent can inspect these files, because the script guarantees they contain no secret values, and gitleaks double-checks.
6. Serialize TOML by hand or with a tiny emitter. stdlib has no TOML writer, and we're not adding dependencies.

### Testing the script without real secrets
- Unit-test the parsing, classification, dotenv escaping, and TOML emitting with a fake API fixture
  (`scripts/tests/fixtures/*.json`) containing obviously fake values.
- Test the sops round trip with a throwaway age key generated inside the test.

**Done:** `scripts/komodo-migrate.py`, tests in `scripts/tests/` (`python3 -m unittest discover -s scripts/tests`).
Notes from implementing it (checked against komodo_client 2.3.2 and the Komodo v2.3.3 source):
- `ListFullStacks` is paginated (default 30 per page), so the script pages until no new stacks come back.
- `ListVariables` and the export return secret Variable values as `###` to non-admin keys. `--discover` and
  `--write-toml` work with any read key. `--write-secrets` needs an admin key.
- The export returns stack environments with values, so its raw TOML is never printed.
- Komodo interpolates `[[VAR]]` into the whole env string, then writes `KEY=<raw value>` lines to `.env`, and
  compose strips the quotes. `sops exec-env` passes values literally, so the script unquotes first. Values
  with `$` or `\` are written but listed under REVIEW, because compose may have expanded them.
- sops echoes bad input lines in its errors. The script hides sops stderr when it contains plaintext.
- Alerter URLs are interpolated by Komodo, so they become `[[KOMODO_ALERTER_<NAME>_URL]]` refs. `webhook_secret`
  and `passkey` are not interpolated, so they're dropped from the TOML and listed. The sync diff will show them.
- `[[refs]]` to names that aren't Komodo Variables (core/periphery config secrets) stay in the TOML as refs.
- `--write-toml` refuses to write if any known secret value (8+ chars) shows up in the output.
- The `komodo` stack is excluded from both write modes by default (Phase 6).
- Extra flags: `--stack` (canary), `--verify` (decrypt round trip, needs the private key), `--out`, `--allow-match`.

## Phase 4: Resource Sync definition (about 20 min)

`komodo/resources/syncs.toml`:
```toml
[[resource_sync]]
name = "docker-stacks"
[resource_sync.config]
linked_repo = "docker-stacks"       # or git_provider/repo/branch, whichever Phase 0 shows
resource_path = ["komodo/resources"]
managed = false                     # flip to true later if UI → git commits are wanted
delete = false                      # flip only after a clean diff
include_resources = true
include_variables = true            # only non-secret variables are in the TOML
include_user_groups = false
```
Also add a GitHub webhook on push to `main` for the sync refresh (`KOMODO_WEBHOOK_SECRET` already exists).
The user creates the sync once in the UI with the same settings. After that, the TOML owns it.

## Phase 5: Canary, then rollout (about 1 h + monitoring)

1. **Backup first.** Take a `mongodump` of the Komodo DB (or use Komodo's own backup to `/backups`) before the first sync execution.
2. The user runs `--discover`, then `--write-secrets`, then `--write-toml`, and commits the output on the branch.
   The agent reviews the diff (names and TOML only).
3. **Canary:** pick one low-risk stack with 1–2 secrets (for example `mealie`, `wizarr` or `spotweb`; confirm with the user).
   Temporarily set the sync's `match_tags` or a canary-only resource path so that only that stack changes.
4. Redeploy the canary from Komodo, then verify:
   - The deploy succeeds and the container is healthy.
   - Each secret variable is set inside the container, using a user-run length check:
     `docker exec <c> sh -c 'printf %s "$VAR" | wc -c'` (non-zero).
   - The Komodo Update log for the deploy contains no secret values. Check with `grep -c` for a known
     value, run by the user.
   - Wrapper cwd assumption holds. If not, switch the wrapper to an absolute path via `[[DOCKER_STACKS_DIR]]/<stack>/secrets.sops.env`.
5. Roll out in batches of about 10 stacks, leaving critical ones for last: `traefik`, `authentik`, `cloudflared`, `pihole`, `komodo`.
6. After everything is green, review the sync diff with `delete = true` and enable it if it's clean.

## Phase 6: Special cases

- **`komodo` stack (bootstrap):** periphery needs sops to deploy the stack that contains periphery. Recommendation:
  keep the `komodo` stack's own secrets (`KOMODO_PASSKEY`, `KOMODO_DATABASE_PASSWORD`, OIDC) out of SOPS in
  round one. Leave them as the Komodo stack environment (not in TOML), or deploy that stack by hand. Revisit later.
- **traefik:** `traefik/dynamic/` uses `{{ env "X" }}`, and those vars reach the container through compose, so the wrapper
  covers them. Verify on a non-critical route first.
- **Multi-line / JSON values** (`SOCIALACCOUNT_PROVIDERS`, `APPRISE_CONFIGS_URLS`): check the dotenv quoting round trip.
- **Build args with secrets** (for example `paseo`, `mcpjungle` with `*_VERSION`/`*_SHA256`, which aren't secrets but are build-time):
  make sure `"build"` is in `compose_cmd_wrapper_include` wherever builds need interpolated secrets.

## Phase 7: Docs and cleanup (about 20 min)

- `CLAUDE.md` + `README.MD`: add a "Secrets" section covering:
  - add or edit a secret: `sops <stack>/secrets.sops.env`
  - add a new stack: create `compose.yaml` plus a `[[stack]]` entry in `komodo/resources/stacks.toml` (plus a secrets file if needed)
  - where the age key lives, and the fact that a leaked key means rotating everything
- Renovate: confirm it picks up the Dockerfile `FROM` lines in `komodo/periphery.Dockerfile`.
- Remove the old per-stack environment secrets from Komodo. The sync does this once the TOML no longer contains them.
  Check the sync diff for it.

## Rollback

- Disable the Resource Sync, restore the Mongo dump, then redeploy the affected stacks.
- Per stack: remove `compose_cmd_wrapper` from the TOML and paste the env back into Komodo from the backup.

## PR breakdown

1. `komodo-periphery-sops`: Phase 1 (Dockerfile, compose change)
2. `sops-scaffolding`: Phase 2 (`.sops.yaml`, hooks, gitignore)
3. `komodo-migrate-script`: Phase 3 script + tests
4. `komodo-resources-canary`: generated TOML + canary stack secrets
5. `komodo-resources-rollout`: remaining stacks, in batches (one PR per batch is fine)
6. `komodo-iac-docs`: Phase 7

## Done when

- [ ] Every Komodo resource except secret Variables and the `komodo` stack's own secrets is declared in `komodo/resources/`
- [ ] The sync diff is empty with `delete = true`
- [ ] Every stack with secrets deploys through the SOPS wrapper and is healthy
- [ ] The repo contains zero plaintext secrets (gitleaks clean, pre-commit hook enforced)
- [ ] The age key is backed up in the password manager
- [ ] Docs are updated

## Time estimate

Agent work is about 5 hours across all phases. User hands-on time is about 45 minutes: key generation, running the
script three times, canary checks, and UI sync creation. Monitoring the rollout batches takes whatever calendar time you like.
