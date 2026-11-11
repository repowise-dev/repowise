---
frontmatter: |
    description: Work with architectural decisions; list, check health, record, pin, or put background records in force.
    allowed-tools: Bash(repowise decision:*), Bash(repowise config list:*), Bash(repowise config explain:*), Read
---

# Repowise Decisions

Repowise keeps architectural decisions (the *why* behind the code) next to
the files they govern and tracks them for staleness. A decision an agent
records comes into force when its commit merges; records mined from history
are background until someone puts them in force. This command drives the
`repowise decision` group. `repowise config list --area decisions` prints
the repository's mode (`decisions.mode`: `full`, `read_only` or `off`) and
every switch, with where each value came from.

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
  `repowise decision add --agent <slug> --decision D --rationale R --affects <path> --evidence-commit <sha>`
  (e.g. `claude_code`, plus `--session <id>` if known), so the record is the
  agent's, not a person's. `--evidence-commit` ties it to the commit the choice
  was made in; an agent's record is accepted only with evidence, from this or
  `--evidence` on confirm. With
  both `--title` and `--decision` it records without prompting and prints the
  id (`--format json` to parse it back). Also optional: `--context`,
  `--alternative`, `--consequence`, `--tag`. `--alternative`, `--consequence`,
  `--affects`, `--tag` and `--evidence-commit` repeat. An agent's record that cites its commit
  comes into force when that commit merges to the default branch.
  **Always state a reason** — pass `--rationale` (or `--context`); a record whose
  body only restates its own title cannot be accepted. `--kind agreement` records
  a working agreement: a rule about how the work is conducted, which names no
  file and is not checked against the code. Bare `repowise decision add` is a
  guided interactive capture (~90s) for a person at a terminal.
- **discover** — `repowise decision discover` prints a prompt: the background
  records on the busiest production files (at most 20) to check against the
  code, put in force signed as you with evidence, or dismiss. Follow it as
  printed. With no background records it has you record the decisions behind
  the busiest files instead. `--agreements` prints the prompt that moves stored
  working agreements into CLAUDE.md, for a team that wants them there.
- **candidates** — `repowise decision candidates --format json` — background
  records not in force, each with its id and the `blockers` that stop it being accepted.
- **confirm** — `repowise decision confirm <id>... --agent <slug> --evidence <commit|file>`
  puts records in force, signed as the agent (plus `--session <id>`
  if known); the acceptance is recorded as the agent's, not a person's.
  `--decision`, `--reason`, `--scope <path>` and `--evidence <commit|file|link>`
  fill the blockers. The unsigned form is a person's. A repository with
  `decisions.agent_acceptance` set to false refuses it; then leave acceptance
  to a person. `confirm` refuses a decision that is already
  accepted; renew that one with `reaffirm`.
- **reaffirm** — `repowise decision reaffirm <id>...` — after re-reading a
  `needs_review` decision that still holds, renew it so it counts as current
  from now. Takes `--reason`, `--evidence`, `--agent <slug>` and `--preview`.
  A background record is refused; put it in force with `confirm`.
- **merge**: `repowise decision merge <id> <into-id>` folds a duplicate
  background record into a decision in force instead of accepting it twice.
  The target must already be in force: confirm it first, or merge the other
  way. `--preview` shows the result without writing.
- **deprecate / dismiss**: `repowise decision deprecate <id> --reason ...`
  (optionally `--superseded-by <id>`, `--preview`) or
  `repowise decision dismiss <id> --reason ...`. An agent passes
  `--agent <slug>` to every review command, and the signer is recorded for
  background records too. A pinned decision refuses both from an agent; a
  person passes `--force`.
- **pin / unpin**: `repowise decision pin <id>` holds a decision in force as
  law (listed by name in CLAUDE.md; agents ask a person before retiring it);
  `repowise decision unpin <id>` releases it. Run them when the user asks,
  with `--agent <slug>`.
- **status**: `repowise decision status` shows what is in force and pinned,
  what came into force in the last seven days and who signed, and what
  capture did.
- **mode**: `repowise config set decisions.mode off|read_only|full` when the
  user asks to turn decisions off or make them read-only; relay the undo
  command it prints. `repowise config explain <key>` says what any other
  decision switch does.

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
