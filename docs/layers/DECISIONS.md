# Architectural Decisions

Repowise finds the reasoning your team already wrote down (ADR files, `# WHY:`
comments, commit messages, pull request bodies) and, if you opt in, the choices
you make in coding-agent sessions. Each record is tied to the files it governs,
backed by a verbatim quote, checked for staleness against the code, and handed
to your agent when it is about to edit those files.

It does not decide anything for you. A machine can propose a candidate; only a
person (or a committed ADR) makes it a decision that governs.

Cost: capture runs inside `repowise init` and `repowise update`. ADR files,
inline markers, manual entry and the conventions source work with no LLM key.
Commit, pull request and comment mining need a model, and are skipped with a
stated reason when no provider is configured. Delivery to agents is a local
SQLite lookup with no network.

## Quick start

```bash
repowise init                        # capture runs as part of indexing
repowise decision candidates         # what is awaiting review
repowise decision confirm a1b2c3d4   # accept one; this is what makes it govern
repowise decision list               # everything, decisions and candidates
repowise decision health             # stale, unscoped, ungoverned hotspots
repowise decision export             # write accepted ones to .repowise/decisions.yaml
```

Record one yourself, from a script or an agent:

```bash
repowise decision add --title "JWT over sessions" \
  --decision "Authenticate API calls with signed JWTs" \
  --rationale "Stateless horizontal scaling" \
  --affects src/auth
```

From an agent over MCP:

```python
get_why(query="why JWT over sessions?")      # search decisions
get_why(query="src/payments/processor.ts")   # what governs this file
get_why()                                    # governance health
```

In the dashboard, the **Decisions** page of a repository lists records by
review lane and lets you accept or dismiss candidates.

```
repowise decision health

  Decision Health

  Active decisions          14
  Proposed (needs review)    3
  Stale decisions            2
  Deprecated                 1

  Stale decisions (2):
    9f3c1a44  JWT over sessions                        (staleness: 0.72)
    2b70de91  EventBus stays in-process                (staleness: 0.58)

  Ungoverned hotspots (1):
    payments/processor.ts
```

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

**Acceptance** is a recorded event, not a status flag. It carries a reason, a
scope, an evidence reference and who signed it. Repowise refuses to store an
acceptance that is missing any of those:

```
$ repowise decision confirm 4b6ddc58
Cannot accept 4b6ddc58: no scope: name the files or modules it governs
Supply the missing parts with --reason, --scope or --evidence.

$ repowise decision confirm 4b6ddc58 --scope src/ingestion
Decision 4b6ddc58 accepted (governing)
```

The acceptance log is append-only. Accepting, re-accepting, withdrawing,
superseding and dismissing each add a row, so the history of who granted and
withdrew authority survives.

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

Internals of each source, the evidence check, the confidence formula and the
session miner: [architecture/decisions.md](../architecture/decisions.md).

## Lifecycle

Stored status is one of `proposed` (a candidate), `active` (accepted),
`deprecated`, `superseded` or `dismissed`. An accepted decision also has a
**currency**, which says whether it still describes the code:

| Currency | Meaning | Still governs? |
|----------|---------|----------------|
| `active` | Accepted, and still describes the code | Yes |
| `needs_review` | Accepted, but at least half of the files it names have changed since it was recorded | Yes |
| `uncheckable` | Accepted, but names no file or module, so it cannot be checked against the code | Agents editing a file are not given it |
| `stale` | Accepted, but every file it names is gone at HEAD (renames followed) | No |
| `superseded` | Replaced by a later decision the person named | No |
| `dismissed` | Authority withdrawn; kept for history | No |

`active`, `needs_review`, `uncheckable` and `stale` are derived from the code
on every read. `superseded` and `dismissed` are set by a person.

**Staleness score.** Each record carries `staleness_score` between 0 and 1: the
fraction of its governed files that have been committed to since the decision
was recorded. A file the repository does not track counts as changed. At 0.5 or
above a record is stale for `decision list --stale-only`, the health summary,
and the `needs_review` currency. A record that names no file scores 0.0 and is
counted separately as unscoped. `repowise decision show` and `get_why` on a
path also ask git directly for one record and print what changed.

**Sticky review.** A dismissed candidate is kept as a tombstone, so re-indexing
never proposes it again. An accepted decision is never walked back to
`proposed` by a later extraction.

**Supersession.** `repowise decision deprecate ID --superseded-by ID2` records
the successor. `repowise update` also marks a decision that a new commit
reversed. Automatic detection of `supersedes` and `conflicts_with` edges by
text similarity is switched off, because similarity between records from one
repository did not reliably mean a shared topic.

## Reviewing candidates

| Command | What it records |
|---------|-----------------|
| `decision confirm ID...` | Accept one or more candidates. `--reason`, `--scope`, `--evidence` fill gaps. `--preview` writes nothing. |
| `decision dismiss ID...` | Tombstone. On an accepted decision this also withdraws its authority. |
| `decision deprecate ID --superseded-by ID2` | Retire with an explicit successor. |
| `decision merge ID INTO_ID` | Fold a candidate into an accepted decision. The old id keeps resolving. |
| `decision dedupe` | Fold duplicate candidates in one sweep. Dry run until `--apply`. |
| `decision split ID` | Flag a candidate as bundling two choices. Never splits it for you. |

In a batch, each id goes through the same acceptance check and is applied on
its own, so one refusal does not stop the rest.

The Decisions page splits records into five lanes that do not overlap:

| Lane | What is in it |
|------|---------------|
| Active | Accepted and still describes the code. These are the rules. |
| Candidates | Never accepted. Governs nothing. Ordered so the ones that can be accepted as-is come first. |
| Needs review | Accepted, but the files it names have moved. Still binds. |
| Uncheckable | Accepted, but names no file or module. |
| History | Accepted, then withdrawn, superseded or dismissed. |

Accept from the UI goes through the same check as `decision confirm`: a
candidate missing a reason, scope or evidence has Accept disabled with the gap
named.

## How to add one

```bash
repowise decision add                                        # guided prompts
repowise decision add --title T --decision D [--affects PATH ...]   # no prompts
```

| Flag | Meaning |
|------|---------|
| `--title`, `--decision` | Both together switch to non-interactive mode and print the new id |
| `--context`, `--rationale` | Why it was needed, why this choice |
| `--alternative` | A rejected alternative. Repeatable |
| `--consequence` | A tradeoff accepted. Repeatable |
| `--affects` | A file or module it governs. Repeatable |
| `--tag` | A tag. Repeatable |
| `--evidence-commit` | A commit the decision was made in. Repeatable |
| `--kind` | `architectural` (default) or `agreement` |
| `--format json` | Machine-readable output with the full id |

What gets stored depends on how you add it:

- **Guided prompts** record an acceptance, signed by you. If an architectural
  decision names no files it is kept as a candidate, because it cannot be
  checked against the code.
- **Flags** (`--title` and `--decision`) store a candidate (`proposed`). A
  person answering the prompts has reviewed the decision; a script or agent
  inferring one has not. Promote it with `repowise decision confirm ID`.
- `--kind agreement` records a rule about how the work is done ("never push
  without review"). It governs the whole repository, needs no files, and
  reaches an agent at session start.

## Governance

**Who may accept.** Each acceptance records who signed it and what kind of
signer: `person`, `agent`, or `import` (the committed manifest or an ADR). The
person defaults to `git config user.name`; `--as` records a different identity.

An agent may withdraw authority (`dismiss`, `deprecate`) by signing as itself
with `--agent SLUG`. Granting authority is refused for an agent unless the
repository allows it:

```bash
repowise decision config agent-acceptance --on    # off by default
repowise decision confirm ID --agent claude_code --session "$SESSION_ID"
```

The dashboard badges any authority record a person did not sign, and
`decision show --format json` returns the signature.

**The tracked manifest.** Accepted decisions belong to the repository, so they
live in a file you commit:

```bash
repowise decision export    # store -> .repowise/decisions.yaml
repowise decision import    # .repowise/decisions.yaml -> store
```

```yaml
# Accepted architectural decisions for this repository.
version: 1
decisions:
- id: 228ddce7b28c4f93a8f1e8976dd1ba4c
  title: Avoid feature gating
  decision: Do not add feature gating.
  reason: Feature flags outlived their purpose here
  scope:
  - packages/core
  currency: active
  source: session
  accepted_at: '2026-09-01T13:04:22+00:00'
  accepted_by: Jane Doe
```

The file is ordered by id with a fixed field order, so a one-line change is a
one-line diff. `export` un-ignores this one file if a `.repowise/` rule in
`.gitignore` was hiding it. On `import` the file wins: new entries are created
and accepted with the file as accepter, changed entries are re-accepted, entries
missing a reason or scope are skipped, and entries the file no longer holds are
left alone (a missing line may be a bad merge). An empty store never overwrites
a non-empty committed file, and a file written by a newer repowise is refused.
Candidates and episodes stay out of the file.

**Capture policy.** Every source can be switched, and so can the model:

```bash
repowise decision config show                 # resolved policy, with reasons
repowise decision config preset local_only    # default | off | local_only | balanced | full
repowise decision source set comment --off
repowise decision source set adr --no-llm     # keep the parse, skip the model
repowise decision llm --off                   # no decision extraction calls a model
```

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

- **Session start.** Repowise scores decisions against the session's likely
  working set (dirty and staged files, branch changes, the previous session's
  edits, branch-name tokens), expands one hop through imports and co-change
  partners, and injects the most relevant under a hard ~400-token cap.
  Candidates come in a separate, labelled section with its own small budget.
  If nothing is relevant enough, nothing is injected.
- **Edit time.** When the agent edits a file an accepted decision governs, it
  gets a one-line notice with the rationale, once per session per decision.

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

## Accuracy and limits

- Every stored field is checked against the verbatim source span. A field that
  is not grounded is cleared; a candidate with no grounded field is rejected.
- Confidence is capped at 0.99 and rises with source authority and independent
  corroboration. It is a ranking aid, not a probability.
- The `session` source ships off: on this repository it produced 139 records,
  none with a context and 115 with neither context nor rationale.
- Automatic supersession and conflict edges are off, so lineage comes only from
  explicit `--superseded-by` and commit reversals.
- Staleness measures whether governed files changed, not whether the decision
  is still true.
- Commit, PR and comment mining need a model; without one, only ADRs, markers,
  conventions, sessions (gates only) and manual entry produce records.

Method for repowise's measured numbers: [BENCHMARKS.md](../BENCHMARKS.md).

## Where it shows up

| Surface | What you get |
|---------|--------------|
| CLI | `repowise decision ...` |
| MCP | `get_why`; decisions inside `get_overview`, `get_context`, `get_risk`, `get_answer` |
| Hooks | Session-start and edit-time injection |
| Dashboard | Decisions page with review lanes |
| Generated files | Accepted decisions in `CLAUDE.md` |
| Code health | `ungoverned_hotspot`, `stale_governance`, `contradictory_decision` findings |

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
| `decision confirm` / `dismiss` / `deprecate` | Accept, tombstone, retire |
| `decision merge` / `dedupe` / `split` | Fold or flag candidates |
| `decision export` / `import` | Round-trip `.repowise/decisions.yaml` |
| `decision migrate` | Classify records from older stores. Dry run unless `--apply` |
| `decision health` | Counts, stale records, ungoverned hotspots |
| `decision status` | What capture did, per source |
| `decision config show` / `preset` / `discovery` / `agent-acceptance` / `capture-prompt` | Capture policy |
| `decision source list` / `set SRC --on/--off [--llm/--no-llm]` | Per-source switches |
| `decision llm --on/--off` | Master switch for model calls |

Full flags: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-decision).
MCP: [`get_why`](../agent/MCP_TOOLS.md#get_why).

## See also

- [architecture/decisions.md](../architecture/decisions.md): source internals, evidence check, confidence, session mining.
- [INTELLIGENCE_LAYERS.md](INTELLIGENCE_LAYERS.md): where decisions sit among the layers.
- [CODE_HEALTH.md](CODE_HEALTH.md): the governance findings.
- [HOOKS.md](../agent/HOOKS.md): the injection hooks.
- [CONFIG.md](../reference/CONFIG.md): the `decisions:` block.
