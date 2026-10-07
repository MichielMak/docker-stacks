# claude-github-app

Lets Claude Code sessions in the Paseo container commit, push and open PRs as the
GitHub App `michiel-claude[bot]` instead of as the user. Mounted read-only at
`/opt/claude-github-app`.

| File | Purpose |
|------|---------|
| `token` | Signs a JWT with the App key and exchanges it for a 1-hour installation token (cached 50 min) |
| `git-credential` | git credential helper that hands out that token |
| `bin/gh` | Wrapper that runs the real `gh` with `GH_TOKEN` set to that token |
| `gitconfig` | Bot author identity, no commit signing, SSH GitHub remotes rewritten to HTTPS |

## Not in the repo (Paseo home volume, `~/.config/claude-github-app/`)

- `private-key.pem` — App private key, mode 600
- `app.env` — `APP_ID=` and `INSTALLATION_ID=`
- `.token` — token cache

## Enabling it for Claude only

`~/.claude/settings.json`:

```json
"env": {
  "GIT_CONFIG_GLOBAL": "/opt/claude-github-app/gitconfig",
  "PATH": "/opt/claude-github-app/bin:/home/paseo/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
}
```

Keep that `PATH` in sync with `PATH` in `../compose.yaml`. Other tools in the container
(Paseo itself, Copilot, your own shell) keep using the user's credentials.
