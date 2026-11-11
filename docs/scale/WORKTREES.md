# Git Worktrees

repowise treats a linked git worktree (`git worktree add`) as a first-class checkout. A new worktree needs no setup: it reads your main checkout's index straight away. For a worktree you keep for a long time, you can give it an index of its own by seeding.

## With no setup: the worktree reads the main checkout's index

A linked worktree that has no `.repowise/` of its own uses the index of the checkout it was created from, as long as that checkout is indexed. repowise finds that checkout from git's own metadata; there is nothing to configure. This covers worktrees you create by hand and the ones coding agents create for parallel work.

What works in the worktree:

| Surface | In a worktree |
|---|---|
| Edit notices (governing decisions, bug-fix history) | Fire for the file you edit, matched by its repo-relative path |
| Session start context and standing decisions | Fire; the context names the main checkout's index and how many files this branch changed |
| Search triage and the flood digest | Fire, ranking the files your search matched |
| Wrong-path rescue | Fires when the suggested file exists in the worktree |
| Re-read and stale-read notices | Fire; they track this session's own reads |
| Decision capture after a commit | Fires; `repowise decision add` in a worktree records into the main checkout's index |
| Command distilling | On or off as the main checkout sets it; filters run with their defaults |
| MCP server (`repowise mcp` started in the worktree) | Serves the main checkout's index; responses flag a served file that differs in the worktree |

What stays quiet, because the index describes the main checkout's indexed commit, not your branch:

| Surface | Why |
|---|---|
| Read skeletons | They cut files at the index's line numbers, which your branch may have moved |
| Zero-result and missing-file search rescues | A search misses most often because the branch moved or removed the symbol the index points at |
| Timed-out glob rescue | It lists the index's paths, which may not exist in the worktree |
| "Index is stale" after a commit | A commit on the branch does not age the main checkout's index |
| Coverage re-ingest | The worktree's coverage is not the main checkout's |
| Edit capture for attribution | It writes events; worktree edits are recovered from transcripts instead |
| Health gate on commit and push | It checks against an index of the checkout itself, which the worktree does not have |

The worktree never writes the main checkout's index. Hook bookkeeping (the session ledger that `repowise hook stats` reads) does land in the main checkout's `.repowise/`, so worktree sessions are counted with the rest. No `.repowise/` is created in the worktree, so the post-commit hook stays inert there and never starts an update.

To turn the fallback off, set `REPOWISE_WORKTREE_FALLBACK=0` in the environment your agent or editor runs in. The hooks and the MCP server then treat the worktree as an unindexed checkout.

## For long-lived worktrees: seed an index of its own

Run either of these inside a worktree whose base checkout is already indexed:

```bash
repowise init        # seeds from the base checkout, then runs an incremental update
repowise update      # same: an unindexed worktree seeds itself first, then updates
```

repowise detects that the directory is a linked worktree, derives the base checkout from git's own metadata (no path needed from you), copies the base's `.repowise/` index, and delegates to the incremental update so only the files that differ on your branch are re-processed. init says so before it hands over:

```
This is a linked worktree of /path/to/base, which is already indexed. init copies that index
and catches it up with repowise update instead of indexing from scratch.
Pass --no-seed for a full init here.
```

The seeded index is fully the worktree's own: the copied database is re-pointed at the worktree, so every surface above answers for your branch, and later updates, searches, and MCP queries stay coherent. With the post-commit hook installed, it stays current on each commit. The copy is the size of the base's `.repowise/` directory, so seeding pays off for a worktree you work in for days, not one an agent uses for a single task. Deleting the worktree deletes its index with it; the base checkout is never modified.

## When seeding is skipped

Auto-seeding only fires when all of these hold; otherwise repowise falls back to a normal full init with a one-line notice explaining why:

- The directory is a linked worktree (its `.git` is a file pointing into the base checkout).
- The worktree has no `.repowise/state.json` yet (an already-indexed worktree is left alone).
- The base checkout has a healthy index (`state.json` + `wiki.db`).
- Base and worktree share the same initial commit, and the base's last synced commit is an ancestor of the worktree's HEAD.
- `--force` was not passed.

## Overrides

| Flag | Effect |
|------|--------|
| `--no-seed` | Force a cold full init inside a worktree; skip auto-detection entirely. |
| `--seed-from <path>` | Seed from an explicit checkout instead of the auto-detected base. Useful for unusual layouts, e.g. seeding one full clone from another. All the same validations apply. |
| `--force` | Re-index this checkout from scratch, in the mode you invoked. Seeding is skipped, because a re-index is the work seeding exists to avoid. Still free: `--force --no-prose` renders the whole wiki from templates with no model. `repowise update --full` remains the only path that regenerates with a model. |

`--seed-from` also works outside worktrees: any two checkouts of the same repository qualify, as long as they share history.

## Workspaces

`--seed-from` maps workspace members by relative path: seeding a workspace root from another workspace root seeds each member repo from its counterpart. Auto-detection currently applies to single-repo worktrees; workspace-member auto-seeding is planned.

## Troubleshooting

- **"does not share the same initial commit"**: the seed source is an unrelated repository (or a shallow clone whose history was truncated). Run a full `repowise init` instead.
- **"is not an ancestor of worktree HEAD"**: the base's index was built on a branch that has diverged from the worktree's branch. Update the base checkout (`repowise update` there) or fall through to full init.
- **"missing .repowise state/db"**: the base checkout was never indexed, or was indexed with a version that predates the current store layout. Index the base first; worktrees created afterwards seed instantly.
