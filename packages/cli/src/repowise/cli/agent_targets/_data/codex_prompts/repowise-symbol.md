---
description: Read one symbol's body with live-verified line bounds (path::Name, live range, or distill omission ref).
---

# Repowise Symbol

Read one function, class, or constant with live-verified line bounds. This is
the CLI adapter over `get_symbol`. Prefer it over a raw file Read when you
already have a `symbol_id` from `/prompts:repowise-context` or `search_codebase`.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/prompts:repowise-init` first." Stop.
2. Resolve the symbol id from `$ARGUMENTS`. If empty, ask for
   `path/to/file.py::Name`, a live range (`path:start-end`), or a
   `repowise#<hex>` omission ref from distill.
3. Run `repowise symbol` and present the body. Report `verified: true/false`
   when shown. If the id is ambiguous, show every matching body — do not
   silently pick one.

## Choosing the invocation

- Named symbol → `repowise symbol "src/api/routes.py::login"`
- Live range read → `repowise symbol "src/api/routes.py:140-180"`
- Distill omission ref → `repowise symbol "repowise#a1b2c3d4e5f6"`
- Extra surrounding lines → `--context-lines N` (0–50)
- Filter restored omission lines → `--query <regex-or-substring>`
- Machine-readable / raw payload → `--format json` / `--full`

```
repowise symbol "src/api/routes.py::login"
repowise symbol "src/api/routes.py:140-180"
repowise symbol "repowise#a1b2c3d4e5f6"
repowise symbol "src/api/routes.py::login" --context-lines 3
```

A truncated body carries a `continuation` you can pass straight back to
`repowise symbol`. Shared targeting flags (`--path`, `--repo`,
`--no-workspace`) match the other tool-adapter commands.

## Notes

- Cheaper and more precise than Read + offset math when you have a
  `symbol_id`.
- For triage (layer / hotspot / callers) without bytes, use
  `/prompts:repowise-context`.
- Never invent source — if the command errors or returns no body, say so.
