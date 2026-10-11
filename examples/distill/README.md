# Distill Example

Walk through `repowise distill` — compress noisy command output before an
agent (or you) reads it — then restore anything that was omitted. No LLM key
required.

## Prerequisites

1. A git repository (any project is fine).
2. `repowise` on `PATH` (`uv tool install repowise` or from this repo:
   `uv sync --all-packages`).
3. Optional but useful for index-aware filters (e.g. smarter `git diff`
   hunks):

```bash
cd /path/to/your-repo
repowise init --index-only --yes
```

`distill` still works without a wiki: it wraps the command, picks a filter by
command shape, and falls back to raw output if distillation would not help.

## 1. Distill a noisy command

```bash
# Test runners — keep failures + summary; collapse pass parades
repowise distill pytest -x
repowise distill npm test

# Git — compact status / log / diff
repowise distill git status
repowise distill git log -20 --oneline
repowise distill git diff main...HEAD

# Builds / installs — errors and what changed, less progress spam
repowise distill npm run build
repowise distill uv sync
```

The wrapper preserves the underlying command's **exit code**, so it is safe in
scripts and CI.

Dropped content is stored under `.repowise/omissions/` and referenced inline:

```text
[repowise#a1b2c3d4e5f6: 230 lines omitted (~6.1k tokens); restore: repowise expand a1b2c3d4e5f6]
```

(The ref above is illustrative — use the hex from your own marker.)

## 2. Expand an omission

```bash
repowise expand a1b2c3d4e5f6              # full original output
repowise expand a1b2c3d4e5f6 -q "FAILED"  # only matching lines
repowise expand "[repowise#a1b2c3d4e5f6: …]"  # pasted whole marker also works
```

MCP clients without a shell can resolve the same refs via
`get_symbol("repowise#<ref>")`.

## 3. See what you saved

```bash
repowise saved                  # per-filter rollup + totals
repowise saved --by day
repowise saved --by source      # distill filters vs mcp:* rows
repowise saved --missed         # commands that looked distillable but weren't rewritten
```

`repowise costs` tracks LLM spend separately; use `saved` for distillation /
MCP-response token savings.

## Smoke checklist

| Step | Expected |
|------|----------|
| `repowise distill git status` | Compact status, or unchanged raw if already tiny |
| Trigger a large test/build run under `distill` | Errors survive; pass/progress noise may become a `[repowise#…]` marker |
| `repowise expand <ref>` | Original omitted bytes restored |
| `repowise saved` | Rollup table (zeros until something was actually distilled) |

## Related docs

- [Distill guide](../../docs/agent/DISTILL.md)
- [CLI: distill / expand / saved](../../docs/reference/CLI_REFERENCE.md)
- [MCP `_meta.omitted`](../../docs/agent/MCP_TOOLS.md#reversible-truncation-_metaomitted)
