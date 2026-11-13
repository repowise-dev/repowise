# Architectural Decisions

Repowise keeps the choices behind your code next to the code they govern. A
coding agent records a decision when it makes one, the decision comes into
force when that work merges, and from then on an agent editing those files is
told about it in one line. People audit and never review a backlog: they pin
the decisions that are law, veto the ones that are wrong, and ask their agent
to edit the rest.

Repowise also reads the reasoning your team already wrote down (ADR files,
`# WHY:` comments, commit messages, pull request bodies). Those mined records
are background: `get_why` serves them when asked, and an agent's discovery pass
puts the real ones in force.

Delivery to agents is a local SQLite lookup with no network. Recording and
reading decisions needs no LLM key; only mining commits, pull request bodies
and comments calls a model.

## Quick start

```bash
repowise init                        # index; mines history once as background
repowise decision discover           # a prompt for your agent: check the background
                                     # records on your busiest files, keep the real ones
repowise decision status             # what is in force, what came in this week
repowise decision pin a1b2c3d4       # hold one as law
repowise decision config mode read-only   # or off, or full (the default)
```

Agents record their own decisions; see
[How decisions are recorded](#how-decisions-are-recorded). The dashboard's
**Decisions** page of a repository is a digest: what is in force, what came in
recently, the background, and history.

## One switch

`decisions.mode` turns the whole layer up or down. Set it with
`repowise decision config mode <off|read-only|full>`.

| Mode | Agents see decisions | Decisions are recorded |
|------|----------------------|------------------------|
| `full` (default) | Yes | Yes |
| `read-only` | Yes: what the repository already holds | No: no mining, no prompt to record, no agent putting one in force, nothing sent anywhere |
| `off` | No: every hook, MCP tool and the generated agent file stay silent | No |

Each mode sets the defaults of the finer switches (capture prompt, agent
acceptance, sync and the rest); one you set afterwards wins, except under
`off`. Details: [CONFIG.md](../reference/CONFIG.md#mode).

## What a decision record is

| | What it is | Reaches agents unasked? |
|---|---|---|
| **Decision in force** | A choice someone stands behind: an agent recorded it and its commit merged, a person or agent accepted it with evidence, or a committed ADR says it is accepted | Yes |
| **Pinned decision** | A decision in force that a person holds as law | Yes, and listed by name in `CLAUDE.md` / `AGENTS.md` |
| **Background record** | Mined from history and not yet put in force. Stored with status `proposed` | No; `get_why` serves it when asked, labelled |

A record carries a title, the decision, context, rationale, rejected
alternatives, consequences, tags, the files or modules it governs (its scope),
its source, a confidence, a staleness score, and one or more evidence rows
(file and line, or commit, plus the quote).

Being in force is a recorded event in an append-only log, not a status flag.
Each entry carries a reason, a scope, an evidence reference and who signed it,
and Repowise refuses one that is missing any of those. A record with no
decision text is refused too; `confirm --decision` supplies it:

```
$ repowise decision confirm 4b6ddc58
Cannot accept 4b6ddc58: no scope: name the files or modules it governs
Supply the missing parts with --decision, --reason, --scope or --evidence.

$ repowise decision confirm 4b6ddc58 --scope src/ingestion
Decision 4b6ddc58 accepted (governing)
```

Putting in force, reaffirming, merging, pinning, withdrawing, superseding,
deprecating and dismissing each add a row, so the history of who granted and
withdrew authority survives.

## How decisions are recorded

### By the agent, at commit time

The agent that made a choice is the one that knows why. It records it in one
call and cites the commit:

```bash
repowise decision add --agent claude_code --decision D --rationale R \
  --affects PATH --evidence-commit SHA
```

The record comes into force when that commit lands on the default branch,
whether by merge, squash or rebase. A commit reverted on the default branch
grants nothing. After a commit whose message states a choice, a hook asks the
agent once a session to record it (`capture_prompt`, on in `full`).

| Flag | Meaning |
|------|---------|
| `--decision` | The choice. With `--agent`, its opening words are the title unless `--title` is given |
| `--context`, `--rationale` | Why it was needed, why this choice |
| `--alternative`, `--consequence` | A rejected alternative; a tradeoff accepted. Repeatable |
| `--affects`, `--tag` | A file or module it governs; a tag. Repeatable |
| `--evidence-commit` | A commit the decision was made in. Repeatable |
| `--kind` | `architectural` (default) or `agreement` |
| `--agent`, `--session` | Sign as this coding agent, with its session id |
| `--format json` | Machine-readable output with the full id |

Adding a title that already exists updates that record. A field the new call
leaves out keeps its stored value, `--evidence-commit` adds to the stored
commits, and the JSON output reports `existed` and the `kept` fields.

`--kind agreement` records a rule about how the work is done ("never push
without review"). It is stored, not delivered: those rules belong in
`CLAUDE.md` or `AGENTS.md`, and `repowise decision discover --agreements`
prints a prompt that moves them there.

### In the pull request description

On repowise.dev, the review bot also reads a short decisions block an agent
writes into the pull request description. Each entry is saved with the pull
request, shown in the bot's comment, and comes into force when the pull
request merges; closing it unmerged drops them. The block is optional, and the
local layer never needs it.

````markdown
<!-- repowise:decisions -->
### Decisions
- Retries live in the HTTP client, not in callers. Why: callers set different
  timeouts and three of them forgot to retry. Files: app/client.py, app/jobs/
<!-- /repowise:decisions -->
````

- Keep the two marker lines exactly as shown; only the first block is read.
- One `- ` bullet per decision: the decision, then `Why:` and the reason, then
  `Files:` and the paths. Indented lines continue the bullet.
- The decision's first sentence is its title (under 80 characters). The
  reason runs 15 to 1,000 characters.
- `Files:` takes 1 to 10 repository-relative paths, comma-separated; a folder
  ends in `/`. No globs or absolute paths.
- At most five entries. Editing the description before merge replaces them.

### By a person

```bash
repowise decision add                                        # guided prompts
repowise decision add --title T --decision D [--affects PATH ...]   # no prompts
```

Guided prompts put the decision in force, signed by you. Flags store it as
background until someone confirms it (`repowise decision confirm ID`).

### From history

| Source | Key | Reads | Needs an LLM key | On by default |
|--------|-----|-------|------------------|---------------|
| ADR files | `adr` | `adr/`, `adrs/`, `docs/adr/`, `docs/adrs/`, `docs/decisions/`, `decisions/`, `architecture/`, `doc/adr/` (up to 60 files); Nygard and MADR headings, YAML frontmatter | No (a model stage is optional) | Yes |
| Inline markers | `inline_marker` | `WHY:`, `DECISION:`, `TRADEOFF:`, `ADR:`, `RATIONALE:`, `REJECTED:` in any comment syntax. Uppercase only, like `TODO:` | No (a model stage is optional) | Yes |
| Commits | `git_archaeology` | Commit messages that carry a decision verb (migrate, switch to, replace, adopt, deprecate, drop, ...) | Yes | Yes |
| Pull request bodies | `pr` | Squash-merge and PR commit bodies that read like a PR description | Yes | Yes |
| Code comments | `comment` | Rationale prose in comments on the most central files | Yes | Yes |
| Agent sessions | `session` | Your local coding-agent transcripts | No for the gates; the structuring step uses a model when one is configured | No |
| Session discovery | `session_discovery` | One broad model pass over new transcript prose per update | Yes | No |
| Conventions | `conventions` | Import edges: a wrapper most files reach a library through | No | No |
| Manual entry | `cli` | `repowise decision add`, the dashboard form | No | Always |
| Agent entry | `agent` | `repowise decision add --agent <slug>` | No | Always |

`repowise init` mines the history window once. `repowise update` reads inline
markers on changed files and makes no model call for decisions unless
`decision config update-mining --on`. A committed ADR whose status says
`accepted` or `approved` and that names what it governs is in force on its
own, with the file recorded as the signer. The same decision found by two
sources becomes one record with two evidence rows.

`repowise decision discover` turns the background into decisions: it prints a
prompt that hands your agent the background records on the busiest production
files (at most 20) to check against the code, put in force with evidence, or
dismiss. With none, the agent records the decisions behind those files. The
dashboard copies the same text.

## People: pin, veto, edit

| Power | How |
|-------|-----|
| Pin | `repowise decision pin ID` holds a decision as law. It is listed by name in the generated agent file, and an agent that tries to retire it is refused and told to ask a person. `decision unpin ID` releases it; it stays in force |
| Veto | `repowise decision dismiss ID` or `deprecate ID --reason R` withdraws a decision. A pinned one needs `--force`, which only a person may pass |
| Edit | Ask your agent: the dashboard's per-decision prompts cover review, pin, unpin, veto and edit |
| Approve | Optional. `repowise decision config approval --on` makes every new decision wait for a person (`decision confirm`), even after its commit lands |

Each signature records who signed and the kind of signer: `person`, `agent`,
or `import` (the committed manifest or an ADR). The person defaults to
`git config user.name`; `--as` records a different identity. Agents sign as
themselves (`--agent claude_code --session "$SESSION_ID"`); the dashboard
badges any decision a person did not sign, and a generated `CLAUDE.md` line
marks it `[accepted by SLUG, not a person]`.
`repowise decision config agent-acceptance --off` keeps granting to people.

`repowise decision status` is the digest for a person: counts in force and
pinned, the decisions that came into force in the last seven days with who
signed and the commit that landed them, and what capture did.

## Lifecycle

An accepted decision has a currency, derived from the code on every read:

| Currency | Meaning | In force? |
|----------|---------|-----------|
| `active` | Still describes the code | Yes |
| `needs_review` | At least half of the files it names changed since it was recorded or last accepted | Yes |
| `uncheckable` | Names no file or module, so it cannot be checked against the code | Not delivered at edit time |
| `stale` | Every file it names is gone at HEAD (renames followed) | No |
| `superseded` | Replaced by a later decision someone named | No |
| `dismissed` | Withdrawn; kept for history | No |

- Staleness score: the fraction of a decision's governed files committed to
  since it was recorded, between 0 and 1. A reaffirm, accept or merge moves
  the start to that moment, so settling a decision clears `needs_review` at
  once. `repowise decision show` and `get_why` on a path ask git which files
  changed.
- Sticky review: a dismissed record is kept as a tombstone, so re-indexing
  never proposes it again.
- Supersession: `repowise decision deprecate ID --superseded-by ID2` records
  the successor. Lineage comes only from that and from commit reversals;
  Repowise does not infer it from text similarity
  ([why](../architecture/decisions.md#supersession-and-conflict-detection)).

## How agents get them

At the moment they matter, with near-zero fixed cost per session (details in
[HOOKS.md](../agent/HOOKS.md)):

| Where | What the agent gets |
|-------|---------------------|
| Editing a governed file | One line per decision in force (up to three per file, each once a session, at most six a session) |
| After a commit | The capture prompt when the message states a choice; with `upkeep_prompt` on, the decisions the commit's files are under and the one command to settle each (still holds, replaced, knowingly broken) |
| `CLAUDE.md` / `AGENTS.md` | One pointer line to `get_why` when any decision is in force, plus the pinned decisions by name |
| Session start | Nothing, unless `decisions.session_start` is on |
| `get_context`, `get_risk`, `get_change_risk` | A one-line pointer to `get_why` when a decision governs a named file; `get_risk` flags stale or contradicted governance |
| `get_answer` | Decisions in force that answer a why-question, cited in the answer |

`get_why` picks its mode from the call shape:

| Call | Returns |
|------|---------|
| `get_why()` | Governance health: counts, stale decisions, ungoverned hotspots |
| `get_why(targets=["src/auth/jwt.py"])` | The decisions in force over the path first, then at most two labelled background records. `include=["history"]` adds superseded and withdrawn decisions, the origin story from git, alignment, archaeology and episodes |
| `get_why(query="why is caching split?", targets=[...])` | Ranked decision search, optionally anchored to paths |
| `get_why(query=..., repo="all")` | The same search across every workspace repo |

When no decision covers a path, `get_why` falls back to git history for that
file, then to a rationale comment in the source. With `mode: off` it answers
only that decisions are off. Full parameters:
[MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_why).

`repowise decision list` and `show` report how often each decision reached an
agent and when last. Code health reports `ungoverned_hotspot`,
`stale_governance` and `contradictory_decision` findings
([CODE_HEALTH.md](CODE_HEALTH.md)).

## Sharing decisions

### The tracked manifest

`repowise decision export` writes the decisions in force to
`.repowise/decisions.yaml`, a file you commit; `repowise decision import`
reads it back. Each entry carries a `base_token` for the exported content:
on `import`, edits apply unless the store has changed since that export,
conflicting entries are skipped and listed in the summary, and
`import --force` overrides the check. Older files without tokens still
apply, and the summary marks those entries as not checked for staleness. Once the file exists,
`repowise update` keeps it current. `update` never creates it, and leaves it
alone when it holds something the index has not read (a colleague's committed
edit, say), with one line saying `decision import` reconciles it. Format and
merge rules: [architecture/decisions.md](../architecture/decisions.md#tracked-manifest).

### Sync with repowise.dev

For a repository also on repowise.dev, `repowise decision config sync --on`
keeps the two in step on every `update`: `decision push` sends the decisions
recorded here, `decision pull` brings down the ones in force there, such as
one the review bot learned from a pull request. A pulled decision keeps its id
there and follows its changes. Sync is off by default, needs
`repowise login` and a GitHub `origin`, and in `read-only` mode only pulls.

### Capture policy

`decision config preset` picks which sources `init` mines,
`decision source set SRC --off` (or `--no-llm` to keep a source's parse and
skip its model stage) switches one source, and `decision llm --off` stops
every decision model call. `decision config show` prints the resolved policy,
with reasons.

| Preset | What it mines |
|--------|---------------|
| `default` | What a config with no `decisions:` block gets: ADR, markers, commits, PR bodies, comments, with the model on |
| `off` | Nothing from history. Agents and people still record |
| `local_only` | ADR, markers and sessions, deterministic only. No model calls |
| `balanced` | `default` minus comments, plus sessions and session discovery |
| `full` | Every source, model on |

Switching a source off stops new mining; it deletes nothing, and decisions in
force keep governing. With no provider configured, model stages report
`skipped`, never `failed`. Full block reference:
[CONFIG.md](../reference/CONFIG.md#the-decisions-block).

## Accuracy

- Every stored field is checked against the verbatim source span. A field that
  is not grounded is cleared; a record with no grounded field is rejected.
- Confidence is capped at 0.99 and rises with source authority and independent
  corroboration. It ranks; it is not a probability.
- The `session` source ships off: of 139 records it produced here, none had a
  context ([measurement](../architecture/decisions.md#session-mining)).
- Staleness measures whether governed files changed, not whether the decision
  is still true; `needs_review` asks an agent to settle it.

## Reference

Every subcommand takes an optional trailing `PATH`, `--format json`, and
accepts an 8-character id prefix. Lifecycle commands exit non-zero on an
unknown id.

| Command | What it does |
|---------|--------------|
| `decision add` | Record a decision (see [How decisions are recorded](#how-decisions-are-recorded)) |
| `decision list` | Table of records with delivery counts. `--status`, `--source`, `--proposed`, `--stale-only` |
| `decision show ID` | Full record with evidence, signature, pin and deliveries |
| `decision candidates` | The background records, and why each was raised |
| `decision discover` | A prompt for your agent to put the real background records in force. `--agreements` moves working agreements into `CLAUDE.md` |
| `decision confirm ID...` | Put in force. `--reason`, `--scope`, `--evidence`, `--decision` fill gaps; `--preview` writes nothing |
| `decision reaffirm ID...` | Still holds: restarts its staleness clock |
| `decision pin ID...` / `unpin ID...` | Hold as law, or release |
| `decision dismiss ID...` | Withdraw and tombstone. `--force` for a pinned one (a person only) |
| `decision deprecate ID` | Retire, with `--superseded-by ID2` or `--reason`. `--force` for a pinned one (a person only) |
| `decision merge ID INTO_ID` | Fold a record into a decision in force. The old id keeps resolving |
| `decision dedupe` | Fold duplicate background records in one sweep. Dry run until `--apply` |
| `decision split ID` | Flag a record as bundling two choices |
| `decision export` / `import` | Round-trip `.repowise/decisions.yaml` |
| `decision push` / `pull` | Send to, and bring down from, this repo on repowise.dev. Needs `config sync --on`; `push --dry-run` needs nothing |
| `decision migrate` | Classify records from older stores. Dry run unless `--apply` |
| `decision health` | Counts, stale decisions, ungoverned hotspots |
| `decision status` | In force, pinned, the last seven days, and what capture did |
| `decision config show` / `mode` / `preset` / `discovery` / `agent-acceptance` / `approval` / `capture-prompt` / `upkeep-prompt` / `session-start` / `sync` / `update-mining` | The policy |
| `decision source list` / `set SRC --on/--off [--llm/--no-llm]` | Per-source switches |
| `decision llm --on/--off` | Master switch for model calls |

Full flags: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-decision).

## See also

- [architecture/decisions.md](../architecture/decisions.md): source internals, evidence check, confidence, session mining, manifest rules, hook scoring.
- [INTELLIGENCE_LAYERS.md](INTELLIGENCE_LAYERS.md): where decisions sit among the layers.
- [CODE_HEALTH.md](CODE_HEALTH.md), [HOOKS.md](../agent/HOOKS.md), [CONFIG.md](../reference/CONFIG.md): governance findings, injection hooks, the `decisions:` block.
