# Keeping the index fresh

repowise answers from its index, so the index should follow your code. Pick the
method that fits your setup; they can be combined.

| Method | Best for | Requires server? |
|--------|----------|-----------------|
| [Post-commit hook](#1-post-commit-git-hook-recommended) | Solo developers, local repos | No |
| [File watcher](#2-file-watcher) | Local development, uncommitted work | No |
| [GitHub webhook](#3-github-webhook) | Teams, a shared repowise server | Yes |
| [GitLab webhook](#4-gitlab-webhook) | Teams, a shared repowise server | Yes |
| [Polling fallback](#5-polling-fallback) | Safety net for missed webhooks | Yes |

Whichever you use, agents are told when the index is behind: the SessionStart
hook reports indexed commit vs `HEAD` ([HOOKS.md](../agent/HOOKS.md)), and MCP
responses carry a `stale_warning` when a file they served has changed.

---

## 1. Post-Commit Git Hook (Recommended)

Runs `repowise update` in the background after every local commit. Your
terminal is never blocked.

`repowise init` installs the hook by default (interactive runs ask first;
`--no-hook` skips it). To manage it yourself:

```bash
repowise hook install              # current repo
repowise hook install --workspace  # every workspace repo
repowise hook status               # add --workspace to check every repo
repowise hook uninstall            # add --workspace to remove from every repo
```

The hook is marker-delimited, so it coexists with other tools' hooks in the
same `post-commit` file.

After each commit, `repowise update` diffs the new commit against the last
synced one, refreshes the graph, git signals, dead code, health and decisions,
and regenerates the wiki pages the change affects (when the repo was indexed
with docs). See [How updates work](#how-updates-work).

---

## 2. File Watcher

Watches the working directory and updates on save. Use it when you want the
index current without committing.

```bash
repowise watch                  # current directory
repowise watch /path/to/repo
repowise watch --debounce 5000  # wait 5s after the last change (default: 2s)
repowise watch --index-only     # no model calls per save
repowise watch --workspace      # every workspace repo
```

Press `Ctrl+C` to stop.

The watcher indexes the working tree, so staged, unstaged and untracked files
all reach the index without a commit. It ignores `.repowise/`, `.git/`,
`node_modules/`, build output and other blocklisted directories, lockfiles and
non-source files, and the files repowise manages itself (`CLAUDE.md`,
`AGENTS.md`, `.mcp.json`, `.claude/`), so an update never triggers itself.

On a repo indexed with docs, each trigger regenerates the changed files' pages
with a model. `--index-only` keeps the index current at no model cost and
leaves the prose for a later `repowise update`.

---

## 3. GitHub Webhook

For a shared repowise server. GitHub sends a push event to the server, which
runs an incremental update.

You need a server reachable from GitHub, and a webhook secret for any server
exposed beyond localhost.

```bash
repowise serve                             # http://localhost:7337
repowise serve --host 0.0.0.0 --port 8080  # custom bind
export REPOWISE_GITHUB_WEBHOOK_SECRET="your-secret-here"
```

In your GitHub repo, open **Settings > Webhooks > Add webhook**:

1. **Payload URL:** `https://your-server.example.com/api/webhooks/github`
2. **Content type:** `application/json`
3. **Secret:** the value of `REPOWISE_GITHUB_WEBHOOK_SECRET`
4. **Events:** **Just the push event**

Push a commit and check **Recent Deliveries**. A working setup returns `200`
with `{"event_id": "...", "status": "accepted"}`.

### Security (GitHub)

With `REPOWISE_GITHUB_WEBHOOK_SECRET` set, every request must carry a valid
HMAC-SHA256 signature in `X-Hub-Signature-256`. A missing `sha256=` prefix or a
mismatched signature gets `401 Unauthorized`.

Without the secret, the server accepts only local (loopback) callers and
rejects everything else with `403 Forbidden`. That keeps `repowise serve`
usable for local testing, but it is not safe for a server reachable from the
internet. Set the secret before exposing the server.

---

## 4. GitLab Webhook

Same idea as GitHub, with a different endpoint and token check.

```bash
export REPOWISE_GITLAB_WEBHOOK_TOKEN="your-token-here"
```

In your GitLab project, open **Settings > Webhooks**:

1. **URL:** `https://your-server.example.com/api/webhooks/gitlab`
2. **Secret token:** the value of `REPOWISE_GITLAB_WEBHOOK_TOKEN`
3. **Trigger:** **Push events**

### Security (GitLab)

With `REPOWISE_GITLAB_WEBHOOK_TOKEN` set, the server compares it with the
`X-Gitlab-Token` header in constant time and rejects a mismatch with
`401 Unauthorized`. Without the token, only local callers are accepted and every
other caller gets `403 Forbidden`, as with GitHub.

---

## 5. Polling Fallback

While `repowise serve` runs, a background job polls every registered repository
every 15 minutes. New commits a webhook missed trigger an incremental update.
No configuration is needed.

---

## How updates work

### `repowise update`

1. Diff the new `HEAD` against the last synced commit (or `--since <ref>`).
2. Re-parse and rebuild the dependency graph, re-index git metadata for the
   changed files, and refresh dead code, health and decision markers.
3. Pick the affected wiki pages: the changed files' pages plus their direct
   importers, within a budget scaled to the size of the change
   (`--cascade-budget N` to override).
4. Regenerate those pages with the LLM, when the repo was indexed with docs.
   Affected pages beyond the budget are marked stale, not regenerated;
   `repowise generate --stale` refreshes them when you choose to spend on it.
5. In a workspace, re-run the cross-repo analysis.
6. Save the new `HEAD` as the last synced commit.

```bash
repowise update                        # diff since last sync
repowise update --since abc123         # diff from a specific commit
repowise update --dry-run              # show affected pages, regenerate nothing
repowise update --cascade-budget 50    # allow more pages (default: auto)
repowise update --index-only           # refresh the index, no model calls
```

### Workspaces

`repowise update --workspace` updates every stale repo, then re-runs the
cross-repo analysis (co-changes, package dependencies, API contracts). Each repo
uses its own docs setting; `--docs` / `--no-docs` override it for the run.
`--repo <alias>` targets one repo. See [WORKSPACES.md](WORKSPACES.md).

### Server sync

`POST /api/repos/{id}/sync` re-parses the repo, re-indexes git metadata, and
rescans dead code and decisions. It does not regenerate wiki pages, so it has no
LLM cost. `POST /api/repos/{id}/full-resync` also regenerates every page.

---

## Environment variables

| Variable | Used by | Description |
|----------|---------|-------------|
| `REPOWISE_GITHUB_WEBHOOK_SECRET` | Server | HMAC secret for GitHub webhook verification |
| `REPOWISE_GITLAB_WEBHOOK_TOKEN` | Server | Token for GitLab webhook verification |

Provider API keys, the database URL and the API token are listed in
[CONFIG.md](../reference/CONFIG.md#environment-variables). Keys saved to
`.repowise/.env` during `repowise init` are loaded by `update` automatically.

---

## Troubleshooting

**"No previous sync found"**: run `repowise init` first.

**"Already up to date"**: the index is already at the latest commit.

**Hook doesn't fire**: make sure the hook file is executable:
`chmod +x .git/hooks/post-commit`. `repowise hook status` shows whether it is
installed.

**Webhook returns 401**: a secret or token is set on the server but the
request's signature did not match (or the GitHub `sha256=` prefix was missing).
Check the value matches on both sides.

**Webhook returns 403**: no secret or token is set, and the request came from a
non-local caller. Set `REPOWISE_GITHUB_WEBHOOK_SECRET` or
`REPOWISE_GITLAB_WEBHOOK_TOKEN`, restart the server, and redeliver the payload.

**Update is slow**: the first update after many commits does more work.
`repowise update --index-only` skips page regeneration when you only need the
index current.
