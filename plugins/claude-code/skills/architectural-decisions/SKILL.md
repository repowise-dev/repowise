---
name: architectural-decisions
description: Why code is built this way, before changing a pattern.
user-invocable: false
---

# Architectural Decisions with Repowise

Repowise keeps the decisions behind this code next to the files they govern.
You record a decision when you make one; it comes into force when your commit
merges to the default branch; after that, an agent editing those files is told
about it in one line. People pin the decisions that are law and veto the wrong
ones; you maintain the rest.

`get_why` picks its mode from what you pass:

1. `get_why(query="why is auth using JWT?")`: decision search.
2. `get_why(targets=["src/auth/service.py"])`: the decisions in force over that
   file first, then at most two labelled background records.
   `include=["history"]` adds the origin story, alignment and withdrawn records.
3. `get_why(query="why was caching added?", targets=["src/auth/cache.py"])`:
   search anchored on the targets.
4. `get_why()`: the decision-health dashboard.

Records mined from history (ADR files, pull request bodies, inline markers,
commits, comments) are background: `get_why` serves them when asked, labelled
as not in force. When no decision covers a path, `get_why` falls back to git
history. If it answers that decisions are off for this repository, do not
record or settle any.

## Before an architectural change

1. Call `get_why(targets=["<the files you are changing>"])`.
2. If a decision in force covers them, follow it, or tell the user before you
   diverge. A pinned decision is law: never retire or replace it yourself; ask
   the user.
3. If none does, proceed.

## Recording a decision you made

When you weighed options and picked one, record it once it is committed:

`repowise decision add --agent <your slug> --decision "..." --rationale "<why, and what you rejected>" --affects <path> --evidence-commit <sha>`

It comes into force when that commit merges, so recording on a branch is safe.
Record choices about the code only; working rules ("always run the linter")
belong in CLAUDE.md or AGENTS.md.

When you open a pull request, you may also list the decisions in its
description. The review bot on repowise.dev reads this block and puts each
entry in force when the pull request merges; without the bot it is plain
text, and the command above is all a local repository needs.

```
<!-- repowise:decisions -->
### Decisions
- Retries live in the HTTP client, not in callers. Why: callers set different
  timeouts and three of them forgot to retry. Files: app/client.py, app/jobs/
<!-- /repowise:decisions -->
```

One bullet per decision: the decision (its first sentence is the title, under
80 characters), then `Why:` and the reason, then `Files:` and 1 to 10
repository paths, comma-separated, a folder ending in `/`. At most five
entries; keep both marker lines exactly as shown.

## Settling a decision your change touches

After a commit that changes governed code you may be asked to settle each
decision it names. Run one command per decision:

- still holds: `repowise decision reaffirm <id> --agent <your slug>`
- your change replaced it: record the new one, then `repowise decision deprecate <id> --superseded-by <new id> --agent <your slug>`
- your change knowingly breaks it: `repowise decision deprecate <id> --reason '<why>' --agent <your slug>`

A pinned decision refuses the last two from an agent; ask the user.

## Checking the background records after an index

When the user asks you to check the decisions, run `repowise decision discover`
and do what it prints. It names the background records on the busiest
production files, at most 20. Keep a record only if an agent editing that code
could do the wrong thing without it; dismiss bug-fix notes and implementation
details. On a repository with no background records it asks you to record the
decisions behind the busiest files instead.

Put one in force with `repowise decision confirm <id> --agent <your slug>`,
adding `--reason`, `--scope <path>`, `--evidence <commit|file|link>` or
`--decision` for whatever a blocker names. Dismiss a wrong one with
`repowise decision dismiss <id> --agent <your slug> --reason ... --yes`, and
fold a duplicate with `repowise decision merge <id> <into-id> --agent <your slug>`
(the target must already be in force). When a decision in force is
`needs_review` and still holds, renew it with `repowise decision reaffirm <id>
--agent <your slug>`. With `--format json` every review command returns
`results[]`, even for one id. Your signatures are recorded as yours, never a
person's. If the repository turned agent acceptance off or is read-only, the
confirm is refused; leave it for a person and mention it.

When the user asks you to pin, unpin or veto a decision, run
`repowise decision pin <id>`, `unpin <id>` or `dismiss <id>` with
`--agent <your slug>`; to edit one, `add` it again with the same title. See
`/repowise:decision`.

If the user states a decision, offer to record it the same way without `--agent`.
