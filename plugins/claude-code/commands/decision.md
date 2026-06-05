---
description: Work with architectural decisions — list, inspect health, add, or confirm auto-proposed decisions.
allowed-tools: Bash, Read
---

# Repowise Decisions

Repowise captures architectural decisions (the *why* behind the code) from eight
sources and tracks them for staleness and conflicts. This command drives the
`repowise decision` group.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `/repowise:init` first." Stop.
2. Map `$ARGUMENTS` to a subcommand, run it, and present the result clearly.

## Subcommands

- **list** (default) — `repowise decision list`
  - "stale" → `--stale-only`; "proposed" → `--proposed`
  - "active" / "deprecated" / "superseded" → `--status <value>`
  - filter by origin → `--source <value>`
- **health** — `repowise decision health` — stale decisions, conflicts, and
  ungoverned hotspots (high-churn files with no recorded decision). Good first call.
- **show** — `repowise decision show <id>` — full record: rationale, evidence
  spans, status, and the supersession lineage.
- **add** — `repowise decision add` — guided interactive capture (~90s). Use when
  the user makes a decision during the session and wants it recorded.
- **confirm** — `repowise decision confirm` — review decisions auto-proposed from
  git history and accept or reject them.
- **deprecate / dismiss** — `repowise decision deprecate <id>` (optionally
  `--superseded-by <id>`) or `repowise decision dismiss <id>`.

## Notes

- For *querying* why code looks the way it does mid-task, prefer the `get_why`
  MCP tool (the `architectural-decisions` skill) — it returns lineage and an
  alignment score. This command is for *managing* the decision records themselves.
- `add` and `confirm` are interactive; tell the user when a step needs their input.
- If unsure of an exact subcommand or flag, run `repowise decision --help`.
