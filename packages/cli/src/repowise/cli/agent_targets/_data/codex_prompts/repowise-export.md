---
description: Export the wiki (or architecture model) to markdown, HTML, JSON, or Structurizr DSL.
---

# Repowise Export

Export indexed wiki pages to files, or emit a Structurizr DSL architecture
model. Use this when the user wants a shareable dump, a static site, a JSON
archive, or a C4/Structurizr model — not when they need an interactive Q&A
answer (`/prompts:repowise-ask`).

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/prompts:repowise-init` first." Stop.
2. Resolve format and output from `$ARGUMENTS` (see below).
3. Run `repowise export` and report where files were written (page count /
   output path). Do not invent page content.

## Choosing the invocation

Default — markdown under `.repowise/export`:
```
repowise export
```

Handle `$ARGUMENTS`:
- "html" / "site" → `repowise export --format html`
- "json" → `repowise export --format json`
- "json full" / "archive" → `repowise export --format json --full`
  (`--full` keeps tombstones and adds decisions / dead code / hotspots)
- "structurizr" / "c4" / "dsl" → `repowise export --format structurizr`
- "structurizr standalone" → `repowise export --format structurizr --standalone`
- "structurizr components" → add `--components`
- "to <dir>" / "-o <dir>" → pass `--output <dir>`
  (for structurizr, a path ending in `.dsl` names the file itself)
- A repo path → `repowise export <path> …`

```
repowise export
repowise export --format html -o ./site
repowise export --format json --full
repowise export --format structurizr --standalone --components
repowise export --format structurizr -o architecture.dsl
```

## Structurizr notes

- Default structurizr output is a **model fragment** to include from your own
  `workspace.dsl`. Pass `--standalone` for a complete workspace with default
  views.
- `--force` overwrites an output file even if Repowise did not write it
  (`--standalone` often targets `workspace.dsl`).
- `--no-externals` leaves third-party dependencies out of the model.

## Notes

- Markdown / HTML / JSON write a directory of pages; structurizr writes DSL.
- Never fabricate export contents. If the command reports no pages, say so and
  suggest `/prompts:repowise-init`.
