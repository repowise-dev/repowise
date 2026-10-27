# Architectural Decisions

Repowise finds the reasoning your team already wrote down (ADR files, `# WHY:`
comments, commit messages, pull request bodies) and, if you opt in, the choices
you make in coding-agent sessions. Each record is tied to the files it governs,
backed by a verbatim quote, checked for staleness against the code, and handed
to your agent when it is about to edit those files.

It does not decide anything for you. Extraction only proposes candidates; a
person, a signed agent, or a committed ADR makes one a decision that governs.

Cost: capture runs inside `repowise init` and `repowise update`. ADR files,
inline markers, manual entry and conventions need no LLM key. Commit, pull
request and comment mining need a model and are skipped with a stated reason
when no provider is configured. Delivery to agents is a local SQLite lookup
with no network.

## Quick start

```bash
repowise init                        # capture runs as part of indexing
repowise decision candidates         # what is awaiting review
repowise decision confirm a1b2c3d4   # accept one; this is what makes it govern
repowise decision list               # everything, decisions and candidates
repowise decision health             # stale, unscoped, ungoverned hotspots
repowise decision export             # write accepted ones to .repowise/decisions.yaml
```

To record one yourself, see [How to add one](#how-to-add-one); agents ask with
`get_why`. The dashboard's **Decisions** page of a repository lists records by
review lane and lets you accept or dismiss candidates.

## What a decision record is

Repowise keeps three things apart:

| | What it is | Can a machine create it? | Does it govern? |
|---|---|---|---|
| **Episode** | An evidenced event: a transcript span, a commit, a structural change | Yes | No |
| **Candidate** | A durable choice inferred from that evidence. Stored with status `proposed` | Yes | No |
| **Decision** | A constraint someone accepted. Stored with status `active` | No, except a committed ADR | Yes |

Only decisions reach your agent as rules, count toward path alignment, or land
in the generated `CLAUDE.md`. Recurrence, confidence and a model's verdict make
a candidate worth reading. None of them accepts it.

A record carries a title, the decision, context, rationale, rejected
alternatives, consequences, tags, the files or modules it governs (its scope),
its source, a confidence, a staleness score, and one or more evidence rows
(file and line, or commit, plus the quote).

Acceptance is a recorded event, not a status flag. It carries a reason, a
scope, an evidence reference and who signed it, and Repowise refuses to store
an acceptance that is missing any of those. A record with no decision text, mined
or typed, is refused too; `confirm --decision` supplies it:

```
$ repowise decision confirm 4b6ddc58
Cannot accept 4b6ddc58: no scope: name the files or modules it governs
Supply the missing parts with --decision, --reason, --scope or --evidence.

$ repowise decision confirm 4b6ddc58 --scope src/ingestion
Decision 4b6ddc58 accepted (governing)
```

The acceptance log is append-only. Accepting, reaffirming, merging,
withdrawing, superseding, deprecating and dismissing each add a row, so the
history of who granted and withdrew authority survives. Only accepted
decisions have rows: dismissing or deprecating a candidate records its signer
(`dismissed_by`, `dismissed_by_kind`, `dismissed_session`) on the candidate's
review row instead. A `deprecated` row withdraws authority like `dismissed`
and is counted as deprecated by `decision health`.

## Where decisions come from

| Source | Key | Reads | Needs an LLM key | On by default |
|--------|-----|-------|------------------|---------------|
| ADR files | `adr` | `adr/`, `adrs/`, `docs/adr/`, `docs/adrs/`, `docs/decisions/`, `decisions/`, `architecture/`, `doc/adr/` (up to 60 files); Nygard and MADR headings, YAML frontmatter | No (parsed directly; a model stage is optional) | Yes |
| Inline markers | `inline_marker` | `WHY:`, `DECISION:`, `TRADEOFF:`, `ADR:`, `RATIONALE:`, `REJECTED:` in any comment syntax. Uppercase only, like `TODO:` | No (a model stage is optional) | Yes |
| Commits | `git_archaeology` | Commit messages that carry a decision verb (migrate, switch to, replace, adopt, deprecate, drop, ...) | Yes | Yes |
| Pull request bodies | `pr` | Squash-merge and PR commit bodies that read like a PR description | Yes | Yes |
| Code comments | `comment` | Rationale prose in comments on the most central files | Yes | Yes |
| Agent sessions | `session` | Your local coding-agent transcripts | No for the gates; the structuring step uses a model when one is configured | No |
| Session discovery | `session_discovery` | One broad model pass over new transcript prose per update | Yes | No |
| Conventions | `conventions` | Import edges: a wrapper most files reach a library through | No | No |
| Manual entry | `cli` | `repowise decision add`, the dashboard form | No | Always |

A committed ADR whose status says `accepted` or `approved` and that names what
it governs accepts its own decision, with the file recorded as the accepter. It
is the only non-human acceptance, and it only applies to a file git tracks. A
draft, an uncommitted file, or one with no status lands as a candidate.

The same decision found by two sources becomes one record with two evidence
rows. Headline fields come from the more authoritative source; the other is
kept as corroboration and raises confidence.

## Lifecycle

Stored status is one of `proposed` (a candidate), `active` (accepted),
`deprecated`, `superseded` or `dismissed`. An accepted decision also has a
currency, which says whether it still describes the code:

| Currency | Meaning | Still governs? |
|----------|---------|----------------|
| `active` | Accepted, and still describes the code | Yes |
| `needs_review` | Accepted, but at least half of the files it names have changed since it was recorded or last accepted | Yes |
| `uncheckable` | Accepted, but names no file or module, so it cannot be checked against the code | Agents editing a file are not given it |
| `stale` | Accepted, but every file it names is gone at HEAD (renames followed) | No |
| `superseded` | Replaced by a later decision the person named | No |
| `dismissed` | Authority withdrawn; kept for history | No |

`active`, `needs_review`, `uncheckable` and `stale` are derived from the code
on every read. `superseded` and `dismissed` are set by a person.

- Staleness score: each record carries `staleness_score` between 0 and 1, the
  fraction of its governed files committed to since the decision was recorded.
  A record written before its evidence commits landed counts from the commit
  (or squash) that landed them. A later accept, reaffirm or merge by a person
  or agent moves the start to that moment, so reviewing a decision clears
  `needs_review` at once. A file the repository does not track counts as
  changed. At 0.5 or above an accepted decision is `needs_review`, and that is
  what `decision list --stale-only` and the health summary count as stale. A record that names no
  file scores 0.0 and is counted separately as unscoped. `repowise decision show` and `get_why` on a
  path also ask git directly for one record and print what changed.
- Sticky review: a dismissed candidate is kept as a tombstone, so re-indexing
  never proposes it again. Retired candidates (dismissed, deprecated,
  superseded) stay out of the candidate queue and the lane totals. A later extraction never walks an accepted decision
  back to `proposed`.
- Supersession: `repowise decision deprecate ID --superseded-by ID2` records
  the successor, and `repowise update` marks a decision that a new commit
  reversed. These are the only sources of lineage; Repowise does not infer
  `supersedes` or `conflicts_with` edges from text similarity
  ([why](../architecture/decisions.md#supersession-and-conflict-detection)).

## Reviewing candidates

`decision confirm` accepts, `dismiss` tombstones, `deprecate` retires, and
`merge`, `dedupe` and `split` tidy candidates ([Reference](#reference)). In a
batch, each id goes through the same acceptance check and is applied on its
own, so one refusal does not stop the rest.

The Decisions page splits records into five lanes that do not overlap. Its
Accept goes through the same check as `decision confirm`, so a candidate
missing a reason, scope or evidence has Accept disabled with the gap named.

| Lane | What is in it |
|------|---------------|
| Active | Accepted and still describes the code. These are the rules. |
| Candidates | Never accepted. Governs nothing. Ordered so the ones that can be accepted as-is come first. |
| Needs review | Accepted, but the files it names have moved. Still binds. |
| Uncheckable | Accepted, but names no file or module. |
| History | Accepted, then withdrawn, superseded or dismissed. |

## How to add one

```bash
repowise decision add                                        # guided prompts
repowise decision add --title T --decision D [--affects PATH ...]   # no prompts
```

| Flag | Meaning |
|------|---------|
| `--title`, `--decision` | Both together switch to non-interactive mode and print the new id |
| `--context`, `--rationale` | Why it was needed, why this choice |
| `--alternative`, `--consequence` | A rejected alternative; a tradeoff accepted. Repeatable |
| `--affects`, `--tag` | A file or module it governs; a tag. Repeatable |
| `--evidence-commit` | A commit the decision was made in. Repeatable |
| `--kind` | `architectural` (default) or `agreement` |
| `--agent`, `--session` | Record it as this agent's (source `agent`), with its session id. Needs the flags |
| `--format json` | Machine-readable output with the full id |

What gets stored depends on how you add it:

- Guided prompts record an acceptance, signed by you. An architectural
  decision that names no files is kept as a candidate, because it cannot be
  checked against the code.
- Flags (`--title` and `--decision`) store a candidate (`proposed`). A person
  answering the prompts has reviewed the decision; a script or agent inferring
  one has not. Promote it with `repowise decision confirm ID`.
- After a commit whose message states a choice, a hook asks the agent once a
  session to record it with `decision add`. On by default;
  `repowise decision config capture-prompt --off` turns it off.
- `--agent SLUG` marks a candidate an agent reasoned out itself. Unlike a
  person's manual entry, accepting it needs an evidence reference
  (`--evidence-commit` on add, or `--evidence` on confirm).
- `--kind agreement` records a rule about how the work is done ("never push
  without review"). It governs the whole repository, needs no files, and
  reaches an agent at session start.

## Governance

### Who may accept

Each acceptance records who signed it and what kind of signer: `person`,
`agent`, or `import` (the committed manifest or an ADR). The person defaults to
`git config user.name`; `--as` records a different identity. An agent reviews
and accepts by signing as itself, for example
`decision confirm ID --agent claude_code --session "$SESSION_ID"`; `merge`,
`dismiss` and `deprecate` take `--agent` too. The acceptance contract is the
same for every signer, and an agent's own record also needs evidence. The
dashboard badges any authority record a person did not sign, the generated
`CLAUDE.md` marks it `[accepted by SLUG, not a person]`, and
`decision show --format json` returns the signature.
`repowise decision config agent-acceptance --off` keeps granting to people:
agents still propose and withdraw.

### The tracked manifest

Accepted decisions belong to the repository, so they live in a file you
commit. `repowise decision export` writes the store to
`.repowise/decisions.yaml` and `repowise decision import` reads it back. Each
entry holds the id, title, decision, reason, scope, currency, source, and when
and by whom it was accepted. On `import` the file wins, and candidates and
episodes stay out of it. Format, merge and ordering rules:
[architecture/decisions.md](../architecture/decisions.md#tracked-manifest).

### Capture policy

Every source can be switched, and so can the model: `decision config preset`
picks a preset, `decision source set SRC --off` (or `--no-llm` to keep a
source's parse and skip its model stage) switches one source, and
`decision llm --off` stops every decision extraction model call.
`decision config show` prints the resolved policy, with reasons.

| Preset | What it runs |
|--------|--------------|
| `default` | What a config with no `decisions:` block gets: ADR, markers, commits, PR bodies, comments, with the model on. Sessions, discovery and conventions off |
| `off` | No capture. Manual entry still works |
| `local_only` | ADR, markers and sessions, deterministic only. No model calls |
| `balanced` | `default` minus comments, plus sessions and session discovery |
| `full` | Every source, model on |

Switching a source off stops new capture; it deletes nothing, and accepted
decisions keep governing. With the model off or no provider configured, model
stages report `skipped`, never `failed`. Full block reference:
[CONFIG.md](../reference/CONFIG.md#the-decisions-block).

`repowise decision status` reports what capture did: the policy, each source
and why it did or did not run, review lanes, backlog age, staging queues and
model spend on decision extraction.

## How agents get them

Through hooks, without asking (details in [HOOKS.md](../agent/HOOKS.md)):

- Session start: Repowise injects the decisions most relevant to the
  session's likely working set under a hard ~400-token cap. Candidates come in
  a separate, labelled section with its own small budget. If nothing is
  relevant enough, nothing is injected. How the working set is scored:
  [architecture/decisions.md](../architecture/decisions.md#hook-delivery).
- Edit time: when the agent edits a file an accepted decision governs, it gets
  a one-line notice with the rationale, once per session per decision.

Through MCP, `get_why` picks its mode from the call shape:

| Call | Returns |
|------|---------|
| `get_why()` | Governance health: counts, stale decisions, candidates awaiting review, ungoverned hotspots |
| `get_why(query="src/auth/jwt.py")` | Three lanes that never mix: `decisions` (accepted, governing; with lineage when there is one), `candidates` (nobody accepted them), `history` (superseded or withdrawn). Plus an origin story from git and an alignment read on the first lane |
| `get_why(query="why is caching split?", targets=[...])` | Ranked decision search, optionally anchored to paths |
| `get_why(query=..., repo="all")` | The same search across every workspace repo |

Only the `decisions` lane is a rule. When no decision covers a path, `get_why`
falls back to git history for that file, then to a rationale comment in the
source. Full parameters: [MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_why).

Accepted decisions also appear in the generated `CLAUDE.md`, `get_overview`,
`get_context`, and as the `governance_risk` flag in `get_risk`. A candidate
never raises `governance_risk`, and reaches `get_answer` labelled `candidate`.
Code health reports `ungoverned_hotspot`, `stale_governance` and
`contradictory_decision` findings ([CODE_HEALTH.md](CODE_HEALTH.md)).

## Accuracy and limits

- Every stored field is checked against the verbatim source span. A field that
  is not grounded is cleared; a candidate with no grounded field is rejected.
- Confidence is capped at 0.99 and rises with source authority and independent
  corroboration. It is a ranking aid, not a probability.
- The `session` source ships off: of 139 records here, none has a context and 115
  have no rationale either ([measurement](../architecture/decisions.md#session-mining)).
- Lineage comes only from explicit `--superseded-by` and commit reversals.
- Staleness measures whether governed files changed, not whether the decision
  is still true.
- Commit, PR and comment mining need a model; without one, only ADRs, markers,
  conventions, sessions (gates only) and manual entry produce records.

## Reference

Every subcommand takes an optional trailing `PATH`, `--format json`, and
accepts an 8-character id prefix. Lifecycle commands exit non-zero on an
unknown id.

| Command | What it does |
|---------|--------------|
| `decision add` | Record a decision (see [How to add one](#how-to-add-one)) |
| `decision list` | Table of records. `--status`, `--source`, `--proposed`, `--stale-only` |
| `decision show ID` | Full record with evidence and signature |
| `decision candidates` | What awaits review, and why each was raised |
| `decision confirm ID...` | Accept. `--reason`, `--scope`, `--evidence` fill gaps; `--preview` writes nothing |
| `decision dismiss ID...` | Tombstone. On an accepted decision this also withdraws its authority |
| `decision deprecate ID --superseded-by ID2` | Retire with an explicit successor |
| `decision merge ID INTO_ID` | Fold a candidate into an accepted decision. The old id keeps resolving |
| `decision dedupe` | Fold duplicate candidates in one sweep. Dry run until `--apply` |
| `decision split ID` | Flag a candidate as bundling two choices. Never splits it for you |
| `decision export` / `import` | Round-trip `.repowise/decisions.yaml` |
| `decision migrate` | Classify records from older stores. Dry run unless `--apply` |
| `decision health` | Counts, stale records, ungoverned hotspots |
| `decision status` | What capture did, per source |
| `decision config show` / `preset` / `discovery` / `agent-acceptance` / `capture-prompt` | Capture policy |
| `decision source list` / `set SRC --on/--off [--llm/--no-llm]` | Per-source switches |
| `decision llm --on/--off` | Master switch for model calls |

Full flags: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-decision).

## See also

- [architecture/decisions.md](../architecture/decisions.md): source internals, evidence check, confidence, session mining, manifest rules, hook scoring.
- [INTELLIGENCE_LAYERS.md](INTELLIGENCE_LAYERS.md): where decisions sit among the layers.
- [CODE_HEALTH.md](CODE_HEALTH.md), [HOOKS.md](../agent/HOOKS.md), [CONFIG.md](../reference/CONFIG.md): governance findings, injection hooks, the `decisions:` block.
