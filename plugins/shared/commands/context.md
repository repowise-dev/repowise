---
frontmatter: |
    description: Triage card for files, modules, or symbols — layer, hotspot, fix history, freshness (relationships, not source bytes).
    allowed-tools: Bash, Read
---

# Repowise Context

Pull a triage card for one or more files, modules, or symbols. This is the CLI
adapter over `get_context`: title, summary, architectural layer, hotspot /
bug-fix history, doc freshness, and optional relationship blocks. Relationships
and risk signals — **not** source bytes by default.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `{{cmd:init}}` first." Stop.
2. Resolve targets from `$ARGUMENTS`. If empty, ask which file / module /
   `path::Symbol` to inspect.
3. Run `repowise context` and present each card: layer, stale bit, hotspot,
   summary. Call out fix history when present.

## Choosing the invocation

TARGETS are file paths, module paths, or `path/to/file.py::Symbol` ids. Batch
them in one call.

- Default triage → `repowise context <targets…>`
- Opt-in blocks (repeatable) → `--include callers|callees|ownership|metrics|decisions|skeleton|…`
- Richer card → `repowise context <targets…> --no-compact`
- Machine-readable / raw payload → `--format json` / `--full`

```
repowise context src/api/routes.py src/api/auth.py
repowise context src/api/routes.py::login --include callers --include metrics
repowise context src/api/routes.py --include skeleton
```

`--include skeleton` adds the body-elided, line-verified file shape. For the
exact function body, prefer `{{cmd:symbol}}` (or `get_symbol`) with a
`symbol_id` from the card.

Shared targeting flags (`--path`, `--repo`, `--no-workspace`) work the same as
the other tool-adapter commands.

## Notes

- Prefer this before opening many files with Read — one call batches cards.
- A target the index cannot resolve returns an `error` card; report that
  plainly instead of inventing structure.
- For a synthesised Q&A answer, use `{{cmd:ask}}`. For "why is it shaped
  this way", use `{{cmd:why}}`.
