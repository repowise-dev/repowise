---
frontmatter: |
    description: Work with architectural decisions — list, inspect health, add, or confirm auto-proposed decisions.
    allowed-tools: Bash, Read
---

# Repowise Decisions

Repowise captures architectural decisions (the *why* behind the code) from
several sources and tracks them for staleness and conflicts. This command drives
the `repowise decision` group. Sources are individually switchable — run
`repowise decision source list` to see which are on; transcript mining is off
unless the repository turns it on.

## Steps

1. If `.repowise/` doesn't exist: "This repo isn't indexed yet. Run `{{cmd:init}}` first." Stop.
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
  the user makes a decision during the session and wants it recorded. With both
  `--title` and `--decision` it records without prompting and prints the id
  (`--format json` to parse it back), which is the form to use with no terminal.
  The rest are optional: `--context`, `--rationale`, `--alternative`,
  `--consequence`, `--affects`, `--tag`, `--evidence-commit`, the last five
  repeatable. A flag-driven record lands `proposed` for a person to `confirm`.
  **Always state a reason** — pass `--rationale` (or `--context`); a record whose
  body only restates its own title cannot be accepted. `--kind agreement` records
  a working agreement: a rule about how the work is conducted, which names no
  file and is not checked against the code. `--evidence-commit <sha>` ties the
  record to the commit the choice was made in.
- **confirm** — `repowise decision confirm` — review decisions auto-proposed from
  git history and accept or reject them.
- **deprecate / dismiss** — `repowise decision deprecate <id>` (optionally
  `--superseded-by <id>`) or `repowise decision dismiss <id>`.

## Notes

- For *querying* why code looks the way it does mid-task, prefer the `get_why`
  MCP tool (the `architectural-decisions` skill) — it returns lineage and an
  alignment score. This command is for *managing* the decision records themselves.
- `confirm` is interactive, and so is `add` unless you pass `--title` and
  `--decision`; tell the user when a step needs their input.
- If unsure of an exact subcommand or flag, run `repowise decision --help`.
