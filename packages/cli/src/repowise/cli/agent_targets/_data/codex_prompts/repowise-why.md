---
description: Why the code is shaped this way — decisions, rationale, and git archaeology (question, path, or decision-health dashboard).
---

# Repowise Why

Answer *why* the code looks the way it does: decision records, rationale, and
git archaeology. This is the CLI adapter over `get_why`. Worth running before a
refactor or a deliberate divergence from a pattern.

`/prompts:repowise-decision` manages decision records (list / add / confirm). This
command **queries** why something is shaped a certain way.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/prompts:repowise-init` first." Stop.
2. Map `$ARGUMENTS` to a mode (below), run `repowise why`, and present
   decisions, alignment, and archaeology. Never invent rationale — if the
   command falls back to git archaeology, say that plainly.

## Modes

- Question → `repowise why "why is auth using JWT?"`
- File path (governing decisions + origin story) → `repowise why src/api/auth.py`
- Target-anchored search → `repowise why "why the retry cap?" --target src/api/client.py`
  (`--target` is repeatable)
- No args → `repowise why` (decision-health dashboard: stale / proposed /
  ungoverned hotspots)
- Machine-readable / raw payload → `--format json` / `--full`

```
repowise why "why is auth using JWT?"
repowise why src/api/auth.py
repowise why "why the retry cap?" --target src/api/client.py
repowise why
```

Shared targeting flags (`--path`, `--repo`, `--no-workspace`) match the other
tool-adapter commands.

## Notes

- Falls back to git archaeology when a path has no decisions, so it is never
  empty — call that out so the user knows it is reconstructed, not recorded.
- For *managing* ADR records (add / confirm / deprecate), use
  `/prompts:repowise-decision`.
- Before contradicting an existing decision, surface it to the user first.
