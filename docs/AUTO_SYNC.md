# Keeping Your Wiki in Sync

repowise can automatically update your wiki whenever code changes. Choose the
method that fits your setup.

| Method | Best for | Requires server? |
|--------|----------|-----------------|
| [Post-commit hook](#1-post-commit-git-hook) | Solo developers, local repos | No |
| [File watcher](#2-file-watcher) | Local development, rapid iteration | No |
| [GitHub webhook](#3-github-webhook) | Teams, CI/CD, hosted repos | Yes |
| [GitLab webhook](#4-gitlab-webhook) | Teams, CI/CD, hosted repos | Yes |
| [Polling fallback](#5-polling-fallback) | Safety net for missed webhooks | Yes |

---

## 1. Post-Commit Git Hook (Recommended)

Runs `repowise update` in the background after every local commit. Your
terminal is never blocked. This is the recommended auto-sync method for
local development.

### Setup

`repowise init` will offer to install the hook automatically at the end
of the setup wizard. You can also install or manage it manually:

```bash
repowise hook install              # install for current repo
repowise hook install --workspace  # install for all workspace repos
repowise hook status               # check if installed
repowise hook uninstall            # remove the hook
```

The hook is marker-delimited, so it safely coexists with other tools'
hooks (linters, formatters, etc.) in the same `post-commit` file.

### What happens

1. You run `git commit`
2. The hook fires in the background (non-blocking)
3. `repowise update` diffs the new commit against the last synced commit
4. Only affected pages are regenerated (typically 3-10 for a small commit)
5. Graph, git stats, dead code, and decisions are also refreshed

### Check hook status

```bash
repowise hook status
repowise hook status --workspace   # check all repos in workspace
```

### Remove the hook

```bash
repowise hook uninstall
repowise hook uninstall --workspace
```

---

## 2. File Watcher

Watches your working directory for file saves and auto-updates. Useful during
active development when you want the wiki to stay current without committing.

```bash
repowise watch              # watch current directory
repowise watch /path/to/repo
repowise watch --debounce 5000  # wait 5s after last change (default: 2s)
```

Press `Ctrl+C` to stop.

Changes to files inside `.repowise/` are ignored to prevent update loops.

---

## 3. GitHub Webhook

For teams or hosted deployments. GitHub sends a push event to your repowise
server, which triggers an incremental update.

### Prerequisites

- repowise server running and reachable from the internet (or GitHub's network)
- A webhook secret (recommended for production)

### Start the server

```bash
repowise serve                          # http://localhost:7337
repowise serve --host 0.0.0.0 --port 8080  # custom bind
```

### Configure the webhook secret

```bash
export REPOWISE_GITHUB_WEBHOOK_SECRET="your-secret-here"
```

### Add the webhook in GitHub

1. Go to your repo **Settings > Webhooks > Add webhook**
2. **Payload URL:** `https://your-server.example.com/api/webhooks/github`
3. **Content type:** `application/json`
4. **Secret:** the same value you set in `REPOWISE_GITHUB_WEBHOOK_SECRET`
5. **Events:** select **Just the push event**
6. Click **Add webhook**

### Verify it works

Push a commit and check the webhook delivery in GitHub (Settings > Webhooks >
Recent Deliveries). You should see a `200` response with:

```json
{
  "event_id": "...",
  "status": "accepted"
}
```

### Security

When `REPOWISE_GITHUB_WEBHOOK_SECRET` is set, every incoming request is
verified using HMAC-SHA256 against the `X-Hub-Signature-256` header. Requests
with invalid or missing signatures are rejected with `401 Unauthorized`.

If the secret is **not** set, signature verification is skipped (convenient for
local development, but not recommended for production).

---

## 4. GitLab Webhook

Same concept as GitHub, different endpoint and auth mechanism.

### Configure the webhook token

```bash
export REPOWISE_GITLAB_WEBHOOK_TOKEN="your-token-here"
```

### Add the webhook in GitLab

1. Go to your project **Settings > Webhooks**
2. **URL:** `https://your-server.example.com/api/webhooks/gitlab`
3. **Secret token:** the same value you set in `REPOWISE_GITLAB_WEBHOOK_TOKEN`
4. **Trigger:** enable **Push events**
5. Click **Add webhook**

### Security

When `REPOWISE_GITLAB_WEBHOOK_TOKEN` is set, the server compares it against
the `X-Gitlab-Token` header using constant-time comparison. Invalid tokens are
rejected with `401 Unauthorized`.

---

## 5. Polling Fallback

When the server is running, a background job polls all registered repositories
every 15 minutes. If new commits are detected that weren't caught by webhooks
(e.g. webhook delivery failed), it triggers an incremental update automatically.

No configuration needed -- this runs automatically when you start the server
with `repowise serve`.

---

## How Updates Work

There are two update paths, depending on the trigger:

### CLI incremental update (`repowise update`)

1. **Diff** -- compare the new HEAD against the last synced commit
2. **Full graph rebuild** -- re-traverse and re-parse all files to rebuild
   the dependency graph (needed for cascade analysis)
3. **Incremental git re-index** -- re-index git metadata only for changed files,
   then recompute percentiles across the whole repo
4. **Dead code analysis** -- run partial dead code detection on changed files
   and their graph neighbors
5. **Decision re-scan** -- re-scan changed files for inline decision markers
6. **Detect affected pages** -- find directly changed files, cascade to 1-hop
   importers, capped by an adaptive budget (auto-scaled based on change size)
7. **Regenerate** -- call the LLM only for affected pages
8. **Cross-repo hooks** -- if the repo is part of a workspace, re-run cross-repo
   co-change and contract analysis
9. **Persist** -- update the database, search index, and record a GenerationJob
10. **Save state** -- record the new HEAD as the last synced commit

A typical single-commit update touches 3-10 pages and completes in under a
minute.

### Server sync (`POST /api/repos/{id}/sync`)

1. **Full re-traverse** -- re-parse all files and rebuild the dependency graph
2. **Full git re-index** -- re-index git metadata for all files
3. **Dead code analysis** -- full dead code scan
4. **Decision extraction** -- full decision marker scan
5. **Persist** -- update the database
6. **Save state** -- record the new HEAD

Server sync does **not** regenerate wiki pages (no LLM cost). Use
**Full Re-index** (`POST /api/repos/{id}/full-resync`) to also regenerate
all wiki pages.

### Workspace updates (`repowise update --workspace`)

Updates all stale repos in parallel (up to 4 concurrent), then re-runs
cross-repo analysis (co-changes, package dependencies, API contracts).
Use `--repo <alias>` to target a single repo within the workspace.

### Dry run

To see what would be updated without actually regenerating:

```bash
repowise update --dry-run
```

### Manual update

```bash
repowise update                        # diff since last sync
repowise update --since abc123         # diff from a specific commit
repowise update --cascade-budget 50    # allow more cascade pages (default: auto)
repowise update --workspace            # update all stale workspace repos
repowise update --repo backend         # update a specific workspace repo
repowise watch --workspace             # auto-update all workspace repos on file change
```

---

## Environment Variables Reference

| Variable | Used by | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | CLI | API key for Gemini LLM provider |
| `OPENAI_API_KEY` | CLI | API key for OpenAI LLM provider |
| `ANTHROPIC_API_KEY` | CLI | API key for Anthropic LLM provider |
| `REPOWISE_GITHUB_WEBHOOK_SECRET` | Server | HMAC secret for GitHub webhook verification |
| `REPOWISE_GITLAB_WEBHOOK_TOKEN` | Server | Token for GitLab webhook verification |
| `REPOWISE_DB_URL` | Server | Database URL (default: local SQLite) |
| `REPOWISE_API_KEY` | Server | Bearer token for API authentication |

API keys can also be stored in `.repowise/.env` (auto-gitignored) during
`repowise init`. The `update` and `reindex` commands load this file
automatically.

---

## Troubleshooting

**"No previous sync found"** -- Run `repowise init` first to create the
initial wiki before using any auto-sync method.

**"Already up to date"** -- The wiki is already synced to the latest commit.
Nothing to do.

**Hook doesn't fire** -- Make sure the hook file is executable:
`chmod +x .git/hooks/post-commit`

**Webhook returns 401** -- Check that the secret/token matches between your
git hosting provider and the environment variable on the server.

**Update is slow** -- If you're catching up on many commits, the first update
may take longer. Subsequent single-commit updates are fast (~30-60s).
