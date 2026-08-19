---
frontmatter: |
    description: Why the code is shaped this way — decisions, rationale, and git archaeology (question, path, or decision-health dashboard).
    allowed-tools: Bash, Read
---

# Repowise Why

Answer *why* the code looks the way it does: decision records, rationale, and
git archaeology. This is the CLI adapter over `get_why`. Worth running before a
refactor or a deliberate divergence from a pattern.

`{{cmd:decision}}` manages decision records (list / add / confirm). This
command **queries** why something is shaped a certain way.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `{{cmd:init}}` first." Stop.
2. Map `$ARGUMENTS` to a mode (below), run `repowise why`, and present
   decisions, alignment, and archaeology. Never invent rationale — if the
   command falls back to git archaeology, say that plainly.

## Modes

- Question → `repowise why "why is auth using JWT?"`
- File path (governing decisions + origin story) → `repowise why src/api/auth.py`
- Target-anchored search → `repowise why "why the retry cap?" --target src/api/client.py`
  (`--target` is repeatable)
- `--target` with no question → answers about those files: one target behaves
  like passing the path, several get a card each
- No args and no target → `repowise why` (decision-health dashboard: stale /
  proposed / ungoverned hotspots)
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

- A *path* falls back to git archaeology when it has no decisions, so it is
  never empty — call that out so the user knows it is reconstructed, not
  recorded.
- A *question* the store cannot answer comes back with no decisions, one
  sentence on why, and a pointer to the tool that fits the question's shape.
  Relay the redirect rather than filling the gap: an empty result there means
  the decision store has nothing, not that the question is unanswerable.
- For *managing* ADR records (add / confirm / deprecate), use
  `{{cmd:decision}}`.
- Before contradicting an existing decision, surface it to the user first.
