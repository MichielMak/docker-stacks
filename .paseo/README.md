# Paseo Hub triggers

One file per trigger in `triggers/`, run by Paseo Hub (`hub/`) on the Paseo daemon.

| Trigger | Starts when | Does |
|---|---|---|
| `paseo-mention` | MichielMak writes `@paseo` in an issue, issue comment or PR comment | Works on the request in a worktree, opens or updates a PR, replies |
| `renovate-needs-agent` | MichielMak adds the `needs-agent` label to a Renovate PR | Checks release notes, fixes the compose files on the PR branch, comments with a risk level |
| `weekly-homelab-check` | Mondays 09:00 Europe/Amsterdam | Read-only Docker and Home Assistant check, summary via Gotify |
| `openchamber-6505-watch` | Daily 09:00 Europe/Amsterdam | Read-only check of openchamber/openchamber#6505 and its fix PR #6508; Gotify message only when a release with the fix ships or the PR is closed. Disable it in the Hub UI once the OpenCode 2.2.5 hold is lifted |
| `mcp-tools-test` | Manual run only | Lists the MCP tools and env vars a Hub agent gets |

## Deploy

From the paseo container (CLI logged in with `paseo hub login https://hub.<domain>`):

```sh
paseo hub deploy --dry-run
paseo hub deploy
```

Each file is created or updated by its `name`. **Deleting a file does not delete the trigger**:
disable or delete it in the Hub UI too.

## Notes

- `run.target.daemon` is the daemon slug Hub assigned at enrollment (the paseo container's
  hostname at that time). If the daemon is re-enrolled or renamed in Hub, update it here.
- Agents push as `michiel-claude[bot]` through the existing git/gh shims in the paseo container
  (see `paseo/claude-github-app/`). No `github:` token block is needed.
- `from_users` is required. Only MichielMak can start a run.
- The weekly check uses `GOTIFY_URL` and `GOTIFY_HEALTH_TOKEN` from the paseo container's env.
