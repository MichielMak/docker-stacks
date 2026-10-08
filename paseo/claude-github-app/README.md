# claude-github-app

Lets Claude Code sessions in the Paseo container commit, push and open PRs as the
GitHub App `michiel-claude[bot]` instead of as the user. Mounted read-only at
`/opt/claude-github-app`.

| File | Purpose |
|------|---------|
| `token` | Signs a JWT with the App key and exchanges it for a 1-hour installation token (cached 50 min) |
| `git-credential` | git credential helper that hands out that token |
| `bin/gh` | Wrapper that runs the real `gh` with `GH_TOKEN` set to that token |
| `bin/git-push-verified` | Pushes the current branch through the GitHub API, so the commits are Verified |
| `bin/git` | Wrapper that sends `git push` to `git-push-verified`; every other git command goes straight to `/usr/bin/git` |
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

## Verified commits

A plain `git push` uploads commits as they are: unsigned, so GitHub shows no "Verified"
badge. The App has no signing key, but GitHub signs commits that an App creates through
the API with its installation token. `git-push-verified` uses that: it recreates the
local commits on GitHub, then points the local branch at the new commits.

Because `bin/git` is first in `PATH`, Claude's normal `git push`, `git push -u origin <branch>`
and `git push --force-with-lease` go through it. You can also run it directly as
`git push-verified` (or `git-push-verified`):

```
git push-verified [-u] [--force] [--resign] [<remote> [<branch>]]
```

- `--force` (or `-f`, `--force-with-lease`): allow replacing commits on the remote branch.
  It refuses if the remote moved since your last fetch.
- `--resign`: also recreate commits that are already on the remote branch, to make old
  unsigned commits Verified. Only for that; it rewrites the branch.
- `GIT_PUSH_VERIFIED=0 git push ...` does a plain, unsigned push.

The wrapper hands these to the real `git push`, unsigned: tags, `--tags`, `--delete`,
`--all`, refspecs other than the current branch, and any other option.

### How it works

1. `git fetch --prune` the remote, and look up the branch on GitHub. The branch may not
   exist yet.
2. Pick the commits to recreate: commits on `HEAD` that no remote branch has. Refuse merge
   commits, and commits whose author isn't `michiel-claude[bot]`.
3. For each commit, oldest first:
   - Diff it against its parent (`git diff-tree -r`).
   - Upload every added or changed file as a blob (`POST git/blobs`, base64, so binary
     files work).
   - Create a tree on top of the parent's tree (`POST git/trees` with `base_tree`). The
     tree has the file mode of each change (`100644`, `100755`, `120000` symlink,
     `160000` submodule), and `sha: null` for deleted files.
   - Check that GitHub's blob and tree hashes are the same as the local ones. Stop if not;
     nothing is pushed then.
   - Create the commit (`POST git/commits`) with the original message and the new parent.
4. Move the branch to the last new commit (`PATCH git/refs`, or `POST git/refs` for a
   new branch). GitHub refuses that if it isn't a fast-forward, unless `--force`.
5. Fetch the branch, and move the local branch to it with `git update-ref`. The trees
   are the same, so the index and working tree don't change.

### Why the REST API and not GraphQL `createCommitOnBranch`

`createCommitOnBranch` is simpler, but it only takes file paths and contents. It can't
set the executable bit, or make symlinks or submodules, and it can't create a branch.
The REST git data API can do all of these, and gives back the blob and tree hashes, so
the script can check that the result is exactly the local commit.

### Limits

- GitHub only signs when the request has no author and no committer. So each commit
  gets `michiel-claude[bot]` as author, `GitHub` as committer, and the push time as date.
  The local author date is lost. The commit hashes change.
- Only commits authored by the bot. Push other people's commits with a plain push.
- No merge commits; rebase first.
- Blobs over 100 MB fail (GitHub API limit).
- Git hooks on push (`pre-push`) don't run.
- Each file change is one API call, so huge commits are slow.
