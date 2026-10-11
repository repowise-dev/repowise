# Hooks & Auto-Sync Example

Keep the wiki index fresh with `repowise hook` (post-commit) and
`repowise watch` (file watcher). No LLM key required when you pass
`--index-only` to the underlying update.

## Prerequisites

1. A git repository with `repowise init` already run once.
2. `repowise` on `PATH` (`uv tool install repowise` or from this repo:
   `uv sync --all-packages`).

```bash
cd /path/to/your-repo
repowise init --index-only --yes
```

## 1. Post-commit hook

Install a hook that runs `repowise update` in the background after each
commit:

```bash
repowise hook install
repowise hook status                   # ✓ post-commit when installed
repowise hook uninstall                # remove when you no longer want it
```

In a workspace, install across every repo:

```bash
repowise hook install --workspace
repowise hook status --workspace
```

## 2. File watcher (uncommitted work)

`watch` re-indexes on save — staged, unstaged, and untracked files — which
the post-commit hook does not cover:

```bash
repowise watch --index-only            # no model calls per save
repowise watch --debounce 5000         # wait 5s after last change
repowise watch --workspace --index-only
```

Press `Ctrl+C` to stop the watcher.

## 3. Agent hook telemetry (optional)

If you use Claude Code agent hooks, inspect what fired:

```bash
repowise hook stats
repowise hook stats --json
```

## Smoke checklist

| Step | Expected |
|------|----------|
| `repowise hook status` | Shows installed / not installed for post-commit |
| `repowise hook install` then `hook status` | `✓ post-commit: installed` |
| `repowise hook uninstall` then `hook status` | `✗ post-commit: not installed` |

## Related docs

- [Auto-sync](../../docs/scale/AUTO_SYNC.md)
- [Agent hooks](../../docs/agent/HOOKS.md)
- [CLI: hook / watch](../../docs/reference/CLI_REFERENCE.md)
