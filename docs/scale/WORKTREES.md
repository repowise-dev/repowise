# Git Worktrees

repowise treats a linked git worktree (`git worktree add`) as a first-class checkout. You do not re-index from scratch: the worktree seeds its index from your main checkout automatically and catches up incrementally.

## What happens automatically

Run either of these inside a fresh worktree whose base checkout is already indexed:

```bash
repowise init        # seeds from the base checkout, then runs an incremental update
repowise update      # same: an unindexed worktree seeds itself first, then updates
```

repowise detects that the directory is a linked worktree, derives the base checkout from git's own metadata (no path needed from you), copies the base's `.repowise/` index, and delegates to the incremental update so only the files that differ on your branch are re-processed. You'll see a one-line notice:

```
[worktree] Linked worktree of /path/to/base detected; seeding its index.
```

Agents and git hooks benefit without any setup: a post-commit hook or a coding agent running `repowise update` inside an unindexed worktree gets the seed-then-update path instead of an error.

The seeded index is fully the worktree's own: the copied database is re-pointed at the worktree, so subsequent updates, searches, and MCP queries stay coherent. Deleting the worktree deletes its index with it; the base checkout is never modified.

## When seeding is skipped

Auto-seeding only fires when all of these hold; otherwise repowise falls back to a normal full init with a one-line notice explaining why:

- The directory is a linked worktree (its `.git` is a file pointing into the base checkout).
- The worktree has no `.repowise/state.json` yet (an already-indexed worktree is left alone).
- The base checkout has a healthy index (`state.json` + `wiki.db`).
- Base and worktree share the same initial commit, and the base's last synced commit is an ancestor of the worktree's HEAD.

## Overrides

| Flag | Effect |
|------|--------|
| `--no-seed` | Force a cold full init inside a worktree; skip auto-detection entirely. |
| `--seed-from <path>` | Seed from an explicit checkout instead of the auto-detected base. Useful for unusual layouts, e.g. seeding one full clone from another. All the same validations apply. |

`--seed-from` also works outside worktrees: any two checkouts of the same repository qualify, as long as they share history.

## Workspaces

`--seed-from` maps workspace members by relative path: seeding a workspace root from another workspace root seeds each member repo from its counterpart. Auto-detection currently applies to single-repo worktrees; workspace-member auto-seeding is planned.

## Troubleshooting

- **"does not share the same initial commit"**: the seed source is an unrelated repository (or a shallow clone whose history was truncated). Run a full `repowise init` instead.
- **"is not an ancestor of worktree HEAD"**: the base's index was built on a branch that has diverged from the worktree's branch. Update the base checkout (`repowise update` there) or fall through to full init.
- **"missing .repowise state/db"**: the base checkout was never indexed, or was indexed with a version that predates the current store layout. Index the base first; worktrees created afterwards seed instantly.
