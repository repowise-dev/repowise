---
frontmatter: |
    description: Work with architectural decisions — list, inspect health, add, or confirm auto-proposed decisions.
    allowed-tools: Bash(repowise decision:*), Read
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
- **add** — an agent records a choice it reasoned out with
  `repowise decision add --agent <slug> --title T --decision D --rationale R --affects <path> --evidence-commit <sha>`
  (e.g. `claude_code`, plus `--session <id>` if known), so the record is the
  agent's, not a person's. `--evidence-commit` ties it to the commit the choice
  was made in, which an agent's record needs before it can be accepted. With
  both `--title` and `--decision` it records without prompting and prints the
  id (`--format json` to parse it back). Also optional: `--context`,
  `--alternative`, `--consequence`, `--tag`. `--alternative`, `--consequence`,
  `--affects`, `--tag` and `--evidence-commit` repeat. A flag-driven record lands as a candidate for
  review; it never governs until accepted.
  **Always state a reason** — pass `--rationale` (or `--context`); a record whose
  body only restates its own title cannot be accepted. `--kind agreement` records
  a working agreement: a rule about how the work is conducted, which names no
  file and is not checked against the code. Bare `repowise decision add` is a
  guided interactive capture (~90s) for a person at a terminal.
- **candidates** — `repowise decision candidates --format json` — records
  awaiting review, each with its id and the `blockers` that stop it being accepted.
- **confirm** — `repowise decision confirm <id>... --agent <slug> --evidence <commit|file>`
  accepts candidates so they govern, signed as the agent (plus `--session <id>`
  if known); the acceptance is recorded as the agent's, not a person's.
  `--decision`, `--reason`, `--scope <path>` and `--evidence <commit|file|link>`
  fill the blockers. The unsigned form is a person's. A repository that
  ran `repowise decision config agent-acceptance --off` refuses it; then leave
  acceptance to a person. `confirm` refuses a decision that is already
  accepted; renew that one with `reaffirm`.
- **reaffirm** — `repowise decision reaffirm <id>...` — after re-reading a
  `needs_review` decision that still holds, renew it so it counts as current
  from now. Takes `--reason`, `--evidence`, `--agent <slug>` and `--preview`.
  A candidate is refused; accept it with `confirm`.
- **merge** — `repowise decision merge <id> <into-id>` — fold a duplicate
  candidate into an accepted decision instead of accepting it twice. The target
  must already be accepted: confirm it first, or merge the other way.
  `--preview` shows the result without writing.
- **deprecate / dismiss** — `repowise decision deprecate <id> --reason ...`
  (optionally `--superseded-by <id>`, `--preview`) or
  `repowise decision dismiss <id> --reason ...`. An agent passes
  `--agent <slug>` to every review command, and the signer is recorded for
  candidates too.

## Notes

- For *querying* why code looks the way it does mid-task, prefer the `get_why`
  MCP tool (the `architectural-decisions` skill) — it returns lineage and an
  alignment score. This command is for *managing* the decision records themselves.
- `add` prompts unless you pass `--title` and `--decision`; the other
  subcommands take ids and flags and never prompt, except `dismiss` (pass `--yes`).
- With `--format json`, confirm, reaffirm, merge, deprecate and dismiss always
  return `{action, preview, results, succeeded, failed}`, even for one id. Read
  `results[i].ok`, `.error`, `.message` and `.blockers`.
- If unsure of an exact subcommand or flag, run `repowise decision --help`.
