# Architectural Decisions

Repowise mines the *why* out of your repo: the ADRs, commit bodies, PR
descriptions, and `# WHY:` comments where your team already wrote down its
reasoning, plus the choices you make in coding-agent sessions. Every record is
tied to the files it governs, backed by a verbatim quote, tracked for staleness,
and pushed back at your agent at the moment it is about to violate or honor it.

## Quick start

```bash
repowise init                      # extraction runs as part of indexing
repowise decision candidates       # what is awaiting review; none of it governs
repowise decision confirm a1b2c3d4 # accept one: this is what makes it govern
repowise decision export           # write the accepted ones to a tracked file
repowise decision list             # everything, decisions and candidates alike
repowise decision health           # stale, conflicting, ungoverned hotspots
repowise decision add              # guided interactive capture
```

## Three things, not one

Repowise distinguishes what it observed, what it inferred, and what you agreed
to, because conflating them is how a machine ends up writing your team's rules.

| | What it is | Can a machine create it? | Does it govern? |
|---|---|---|---|
| **Episode** | An evidenced event: a transcript span, a commit, a structural change | Yes | No |
| **Candidate** | A durable choice inferred from that evidence | Yes | No |
| **Decision** | A constraint you accepted | No, except a committed ADR | Yes |

Only the third reaches your agent, scores path alignment, or lands in the
generated `CLAUDE.md`. Recurrence, confidence, a model's verdict and a
successful implementation are all evidence that a candidate is worth reading;
none of them is an acceptance.

**Acceptance is a recorded event**, not a status somebody set. It carries a
reason, a scope, an evidence reference, and who accepted it, and repowise
refuses to store one missing any of those rather than filling in a blank:

```
$ repowise decision confirm 4b6ddc58
Cannot accept 4b6ddc58: no scope: name the files or modules it governs
Supply the missing parts with --reason, --scope or --evidence.

$ repowise decision confirm 4b6ddc58 --scope packages/core/src/repowise/core/ingestion
Decision 4b6ddc58 accepted (governing)
```

The log is append-only. Re-accepting after review, sending a decision back to
review, superseding it and withdrawing it each add a row, so the history of who
granted and withdrew authority survives every later action.

### Upgrading from before the split

Before this, a record reached `active` by appearing in two coding-agent
sessions, with nobody involved. `repowise decision migrate` classifies those:

```
$ repowise decision migrate

Decision migration (dry run)

  Total legacy records           504
  Kept as decisions                0
  Reclassified as candidates     502
  Dismissed tombstones             2
  Already migrated                 0

  Why records became candidates:
     381  never accepted: it was awaiting review
     121  active by recurrence, not by a person: a session row carries no acceptance event
```

**Expect your standing decisions to shrink**, and on many repositories to
empty. That is the correction, not a regression: those records were never
accepted, and everything is still there under `repowise decision candidates`,
ready to be accepted deliberately. Nothing is deleted, every id keeps
resolving, retirements you performed are preserved, and re-running changes
nothing. `repowise init` and `repowise update` run the same classification, so
a store cannot sit half-migrated with the status column and the acceptance log
disagreeing.

## The tracked manifest

Accepted decisions are the one part of this layer that belongs to the
repository rather than to your machine, so they live in a file you commit:

```bash
repowise decision export   # store -> .repowise/decisions.yaml
repowise decision import   # .repowise/decisions.yaml -> store
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
  accepted_by: Raghav
```

Ordered by id and rendered from a fixed field order, so two machines holding
the same decisions produce the same bytes and a one-line change is a one-line
diff. Written atomically and fsynced; an unchanged render is not rewritten at
all. Comments are not preserved — it is generated, and says so in its own
header.

`export` un-ignores the file in your `.gitignore` if a `.repowise/` rule was
hiding it, leaving everything else under `.repowise/` ignored, including nested
ones elsewhere in the tree.

On import the file wins: it is what a colleague's commit changed and the store
is the copy. A decision the file has and the store does not is created and
accepted with the file itself as the accepter — the only non-human accepter
there is, and only because it is version controlled and reviewable. An entry
that differs is re-accepted from the file. An entry missing a reason or a scope
is skipped rather than guessed at, and an entry the file no longer holds is
left alone: a missing line is as likely a bad merge as a deliberate removal.

Two guards on the write. A store with no acceptances refuses to overwrite a
non-empty committed file, because deleting the index is a documented recovery
step and it must not delete your decisions with it. And a file written by a
newer repowise is refused rather than silently downgraded.

Episodes and candidates stay out of it. They are evidence and inference, they
turn over constantly, and committing them would put a machine's opinions under
review as though they were the team's.

## Review actions

| Command | What it records |
|---------|-----------------|
| `decision confirm ID...` | Accept, optionally editing the reason and scope on the way. Takes many ids. |
| `decision confirm ID` on a decision | Reaffirm it after review. |
| `decision merge ID INTO_ID` | Fold a candidate into an existing decision. The old id resolves to the target. |
| `decision split ID` | Flag a candidate as bundling two choices. Never splits it for you. |
| `decision dismiss ID...` | Tombstone it. On an accepted decision this also withdraws its authority. Takes many ids. |
| `decision deprecate ID --superseded-by ID2` | Retire it with an explicit lineage edge. |

`confirm` and `dismiss` take a list because a repository accumulates candidates
faster than anyone reviews them one command at a time. Every id in a batch goes
through the same acceptance contract as a single one, and each is applied on its
own, so one refusal commits the rest and reports itself instead of ending the
run. `--preview` runs the batch through that contract and rolls it back, so the
report is what the write would have said rather than a second opinion about it.

Similarity never supersedes anything: an edge exists because somebody named the
successor. Merging and superseding retire ids that may already be written down
somewhere, so both leave an alias and the retired id keeps resolving.

The same actions are on the Decisions page, split into five review lanes that
partition the repository, so the counts add up and no record is in two of them:

| Lane | What is in it |
|------|---------------|
| Active | Accepted, and still describes the code it names. These are the rules. |
| Candidates | Never accepted. Governs nothing, reaches no agent. Carries the evidence quote it was drawn from, so it can be accepted or dismissed from the row. Ordered so the ones the acceptance contract would take come first. |
| Needs review | Accepted, but the files it names have moved. Still binds. |
| Uncheckable | Accepted, but names no file or module, so nothing can check it against the code and no agent editing a file will be given it. |
| History | Accepted and then withdrawn, superseded or dismissed. |

The lane is a join onto the acceptance, not a filter on the status column, so
`GET /api/repos/{id}/decisions?lane=candidates` and
`GET /api/repos/{id}/decisions/lane-counts` are different questions from
`/decisions/counts`, which groups the status projection. Every listed record
also carries a `currency` field; a record with no `currency` is a candidate.
The lane is applied before the page is cut, so a page of a lane is a page of
that lane.

A candidate carries a review row, written where it is captured and refreshed
for every open candidate on each index. It records which lane raised it, the
version that extracted it, whether the lane saw the candidate bundling more
than one choice, and whether the record names any scope at all. It also carries
the review priority the Candidates lane and `repowise decision candidates`
order on, which is the acceptance contract's verdict as of the last index and
nothing else: a reviewer meets the candidates they can finish first, and
confidence and recency break the ties. Re-extraction refreshes those signals
but never the review state, so a dismissal or a split request survives the next
index.

A store indexed before the row existed has candidates without one. Until the
next index writes them a row they rank as unjudged, alongside the ones the
contract refused, and `repowise decision status` counts them so the gap is
visible rather than silent.

Accepting from the UI goes through the same acceptance contract as
`repowise decision confirm`, so a candidate missing a reason, a scope or an
evidence reference has its Accept disabled with the gap named beside it rather
than failing after the click. Dismissing stays available: a candidate that
cannot be accepted is the one most worth tombstoning.

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

`repowise decision health` scores governance. `repowise decision status` reports
capture: the effective policy and preset, every source with the reason it made
no call, what each has captured and when, the review lanes, how much of the
unreviewed backlog is ready to accept against how much is blocked, the age of
that backlog, the staging queues, and the model spend booked to decision
extraction.

Nothing records a capture run. The extraction report and the broad lane's
discovery report are rendered to the progress line and discarded, and
`.repowise/state.json` carries nothing per source, so `status` derives every
figure from a durable trace instead: the records and their review rows, the
staging queues, and the `decision_extraction` cost rows. Two things follow, and
the command says both rather than papering over them. Spend is all-time with
the last call named, because no stored boundary says which call belonged to
which run. And a queue that a store predates is reported absent, not as zero.

From an agent:

```python
get_why(query="why JWT over sessions?")            # NL search
get_why(query="src/payments/processor.ts")         # what governs this file
get_why(query="why is caching split?", targets=["src/cache"])
get_why()                                          # health dashboard
```

## Where decisions come from

Five capture sources run at index time, each a pass over the repo and its git
history.

| Source | Key | Reads | Notes |
|--------|-----|-------|-------|
| ADR files | `adr` | `adr/`, `adrs/`, `docs/adr/`, `docs/adrs/`, `docs/decisions/`, `decisions/`, `architecture/`, `doc/adr/` | Nygard and MADR headings plus YAML frontmatter, parsed without an LLM. Up to 60 files. A document that explicitly says `accepted`/`approved`, is committed, and names what it governs accepts its own decision, with the file recorded as the accepter; it is the only non-human acceptance there is. A draft, an uncommitted file, or one with no Status section lands as a candidate. |
| Inline markers | `inline_marker` | `# WHY:` / `# DECISION:` / `# TRADEOFF:` / `# ADR:` / `# RATIONALE:` / `# REJECTED:` | Any comment syntax (`#`, `//`, `--`, `/*`, `*`). The keyword is case-sensitive and must be capitalised, like `TODO:` and `FIXME:` — otherwise ordinary prose ("# Rejected: nothing to extract.") becomes an architectural decision. Up to 5 continuation lines, plus 20 lines of surrounding context. Fenced code blocks in Markdown are skipped. |
| Git archaeology | `git_archaeology` | Commit messages | Gated on 19 decision verbs (migrate, switch to, replace, adopt, deprecate, drop, rewrite, split, revert, and the rest). |
| PR bodies | `pr` | Squash-merge and PR commit bodies | A body only qualifies when it looks like a PR description (`## Why`, `## Motivation`, `## Context`, `Closes #`, `Before:` / `After:`). Up to 25 bodies. |
| Code comments | `comment` | Block comments and docstrings on high-centrality files | Bounded to 30 nodes, and to prose carrying a rationale cue ("because", "instead of", "rather than", "trade-off", "we chose", "deliberately"). Centrality-bounded on purpose: comment archaeology across a whole repo is noise. |

Three more sources sit outside the index-time set: `session` (deterministic
gates over your coding-agent transcripts, below), `session_discovery` (one
broad model pass over the same transcript prose, below, off unless you enable
it), and `cli` (a decision you typed yourself, the most authoritative source
there is).

### Controlling capture

Every source is switchable, and so is the model. One resolved policy backs the
CLI, the API, and the index pipeline, so what `config show` prints is what the
next `init` or `update` will actually run.

```bash
repowise decision config show           # resolved policy, per source, with reasons
repowise decision config preset local_only
repowise decision source list
repowise decision source set comment --off
repowise decision source set adr --no-llm   # keep the parse, skip the model
repowise decision source set session_discovery --on   # the broad lane, opt-in
repowise decision config discovery --max-sessions 6   # and its per-update budget
repowise decision llm --off                 # no decision extraction reaches a model
```

```
repowise decision config show

  Decision capture on  ·  LLM extraction off  ·  preset local_only
  No LLM provider configured; model stages are skipped.

Source           Status              LLM  Why
inline_marker    deterministic_only  no   Decision LLM extraction is off. The deterministic stage still runs.
git_archaeology  disabled            no   This source is switched off.
adr              deterministic_only  no   Decision LLM extraction is off. The deterministic stage still runs.
pr               disabled            no   This source is switched off.
comment          disabled            no   This source is switched off.
session            deterministic_only  no   Decision LLM extraction is off. The deterministic stage still runs.
session_discovery  disabled            no   This source is switched off.
cli                always_on           -    Manual entry is always available.

  Discovery budget: up to 12 session(s), 30,000 input tokens per update.
```

The presets are `default`, `off`, `local_only`, `balanced`, and `full`; editing
any individual switch afterwards reads as `custom`. `default` is what a config
with no `decisions:` block resolves to, and it is deliberately not `full`: a
source added after those switches existed stays off until you ask for it, so an
upgrade never starts a model call you did not enable. A stored preset that
lists its sources is treated the same way: that list is what the preset
covered when it was written. Re-apply the preset to pick up a source added to
it since. Mutations take `--dry-run` and
`--format json`, write atomically, and preserve every unrelated key in
`config.yaml`.

Three things the switches deliberately do **not** do. Turning a source off stops
new capture from it; it deletes nothing already stored. A decision you have
already accepted keeps governing after the source that found it is switched off,
because authority comes from your acceptance and not from the source staying on.
And `llm: false` is a complete mode rather than a broken one: transcripts are
still read, markers and ADRs still parsed, episodes still recorded, manual
entry unaffected. Status says *skipped*, never *failed*.

`skipped_no_provider` is the same idea for a missing API key: an LLM-only source
reports that it had nothing to run with, and a hybrid source falls back to its
deterministic stage.

Full block reference: [CONFIG.md](../reference/CONFIG.md).

## Evidence: verified, fuzzy, unverified

Every produced field (`decision`, `rationale`, `source_quote`) is checked against
the verbatim source span the extractor recorded. This is the anti-hallucination
gate, and it matters most for the generated sources: the page generator *writes*
candidate decisions rather than only reading them, and the gate is what stops a
fluent invention from being stored as institutional memory.

| Verdict | Fires when | Effect on confidence |
|---------|-----------|----------------------|
| `exact` | The normalized quote is a substring of the source span. | No penalty. |
| `fuzzy` | Token overlap with the source span is at least 0.6 (a paraphrase or a reflow). | Multiplied by 0.85. |
| `unverified` | Neither, or there was no source span to check against. | Multiplied by 0.6. |

An ungrounded field is cleared, not kept. A candidate whose every produced field
is ungrounded is rejected outright. A candidate with no source text at all is
kept but stamped `unverified`: repowise never fabricates a rejection it cannot
justify.

Confidence then rises with how authoritative the source is and how many
independent sources corroborate it:

```
confidence = 0.4 + 0.5 * (best_source_rank / 9)
           + min(0.12, 0.04 * (corroborating_sources - 1))
           x verification penalty
```

The rank ladder is `cli` 9, `session` 8, `adr` and `pr` 7, `commit` and
`git_archaeology` 6, `inline_marker` 4, `comment` 3, and the heuristic tiers
below that. `session` sits above `adr` because a transcript carries what a person
actually said while deciding, and a document is someone's later write-up of it;
with the order reversed the write-up overwrote the words. Retired sources keep
their rungs (`changelog` 5, `readme_mining` 3) so rows written before their
removal still rank instead of dropping to the unknown-source floor. The result is clamped to
`[0, 0.99]`: nothing is ever certain.

**Sources corroborate, they do not overwrite.** The same decision found in an ADR
and in a commit body becomes one record with two evidence rows. Headline fields
come from the highest-ranked row; the lower-ranked one is kept as corroboration
and pushes confidence up. A decision resting only on a plain code comment is
decayed a further 0.85 so it never reads as confident as an ADR.

## The decision graph

Decisions are not a flat list. Typed edges connect them:

| Edge | Meaning |
|------|---------|
| `supersedes` | The newer decision replaces the older one, and the older record flips to `superseded`. |
| `refines` | Narrows or extends a decision without reversing it. |
| `relates_to` | Same topic, no ordering claim. |
| `conflicts_with` | Two *active* decisions contradict each other. A governance smell, surfaced in `decision health` and in the code-health layer. |

**Automatic detection of these two edges is currently off.** It scoped a
conflict by embedding similarity: two decisions had to share a topic (at least
two shared content tokens after stopword removal) and then either straddle an
opposing verb pair (`adopt` / `use` / `introduce` against `drop` / `remove` /
`deprecate` / `revert`) or carry a reversal signal ("replace", "migrate",
"switch to", "no longer", "in favor of"), with an LLM tiebreaker on the pairs
the heuristic could not call. In practice similarity does not scope: among
descriptions drawn from one repository, a cosine of 0.81 is the baseline rather
than evidence of a shared topic, so the check fired between unrelated records
and retired records that were correct. It returns when a conflict is scoped
structurally — by two decisions touching the same code — with similarity used
only to rank the candidates that test finds.

It was the only writer of these edges, so **no edges exist while it is off** and
lineage is empty. Two things still work: the diff-driven evolution pass on
`repowise update` marks a decision that a new commit reversed, and `repowise
decision deprecate --superseded-by ID` records the successor on the record
itself (the `superseded_by` column, not an edge). Records the detector retired
before it was turned off are restored to `proposed` on the next `init` or
`update`, and its edges are deleted in the same pass.

`supersedes` and `refines` chain into a **lineage**, so `get_why` can answer
"why is auth structured this way?" with `sessions -> JWT -> OAuth2` rather than
three disconnected records. `get_why(query="<path>")` returns the lineage
whenever the chain has more than one node.

Edges accrete rather than clobber, and confirmations are sticky in both
directions: `repowise decision dismiss` keeps a `dismissed` tombstone so
reindexing never re-proposes the same thing, and a confirmed `active` decision is
never walked back to `proposed` by a later extraction.

## Staleness

A decision is only useful while it still describes the code, and that is a
question the repository can answer rather than one worth guessing at. Every
record carries a `staleness_score` between 0 and 1: **the fraction of its
affected files that have been committed to since the decision was recorded.**

- None of them has changed: `0.0`.
- All of them have: `1.0`.
- A file the repository does not track counts as changed, because a record
  naming something absent cannot be shown to still hold.

`>= 0.5` is stale everywhere: `repowise decision list --stale-only`, the health
summary, and the staleness column in the CLI table.

The number is a supporting fact; what an accepted decision *is* gets a word:

| Currency | Meaning |
|----------|---------|
| `active` | Accepted, and still describes the code. |
| `needs_review` | Accepted, but the files it names have moved. Still governs — a decision whose code moved is one to re-read, not one to ignore. |
| `uncheckable` | Accepted, but names nothing the repository can be asked about. |
| `superseded` | Replaced by a later decision, through an explicit lineage edge. |
| `dismissed` | Authority withdrawn, kept for history. |

The first three are derived from the code on every read, so a decision moves
between them without anybody touching it. The last two are states a person set,
and the repository does not get to argue with them.

There is deliberately no constant in that definition. An earlier version grew
the score with 90-day commit volume and record age, and added a boost when a
later commit *message* contained words like "migrate away"; both were fitted to
one repository's history and to English commit prose, and the result was a
number that moved for reasons unrelated to whether the code had moved.

**A record that names no file scores 0.0 and is not fresh** — the question
cannot be asked of it at all. `repowise decision health` counts those
separately, as *unscoped*, rather than banking them as current.

For one record on demand, `repowise decision show` and `get_why` on a path go
further and ask git directly, printing what it says: *"nothing in the 3 files it
governs has changed since 2026-05-02"*. That costs a subprocess, so it is served
only where one is affordable — never from an editor hook or during an update.

## Driving decisions from an agent

Every subcommand takes `--format json`. The lifecycle commands (`confirm`,
`dismiss`, `deprecate`, `show`) exit non-zero on an unknown id and emit
`{"error": "decision_not_found", "decision_id": "..."}`, so a scripted confirm
cannot be mistaken for a successful one; `dismiss` skips its prompt under
`--format json` or with `--yes`. `decision config show --format json` returns
the whole source registry with a `status` and a `reason` per source, which is
the machine-readable answer to "will this source run, and if not why not".

Reading decisions back, `active` means somebody accepted it and it binds;
anything else is a candidate and is a hint. The stored status is a projection
of the acceptance event, not the authority itself — the acceptance is — but the
two are kept in step, so a reader that only has the status column stays correct
rather than merely stale. See [MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_why).

## Session-mined decisions

`repowise update` (docs mode) reads your local coding-agent transcripts and mines
the durable decisions out of them: user corrections, explicit choices with a
stated reason, and failed approaches replaced by working ones. Claude Code
transcripts come from `~/.claude/projects/`, read incrementally from a cursor so
each line is processed once.

Three stages, in order:

1. **Deterministic gates.** A user correction needs a pushback lead ("no,",
   "don't", "not like that", "actually,", "instead"). An explicit choice needs a
   decision verb ("use", "went with", "switched to", "chose", "always", "never")
   *paired* with a causal marker. A dead end needs three consecutive failures of
   the same command anchor.
2. **One batched LLM structuring call per update**, capped at 60 candidates.
   Every produced field must quote the transcript verbatim or it is dropped;
   an ungrounded `source_quote` rejects the candidate.
3. **Promotion to a candidate.** A decision seen in two or more distinct
   sessions, or one direct user correction, is written with `source: session`
   as a candidate. Recurrence is evidence that a candidate is worth reading,
   not an acceptance event: `repowise decision confirm` is what makes it
   govern. Repeated observations accrete evidence rows and raise confidence
   without ever creating authority.

Everything stays on your machine. Transcripts are read locally, staging lives in
`.repowise/sessions/sessions.db`, and only the distilled decision text about the
codebase is stored. Turn the pipeline off with
`repowise decision source set session --off`, or keep the transcript reading and
drop only the structuring call with `--no-llm`.

### Broad session discovery

Those gates are precision-first, and they are the recall bottleneck: a decision
settled in a paragraph that never opens with "no," or pairs a decision verb
with a causal cue is invisible to them. `session_discovery` is the recall lane
beside them, off unless you enable it:

```bash
repowise decision source set session_discovery --on
```

Once per update it makes **one** model call over the user and assistant prose
that update newly read, bounded by `discovery.max_sessions` (default 12) and
`discovery.max_input_tokens` (default 30,000). It reuses the transcript read
the `session` source already performs, so enabling it does not read your
transcripts twice, and it costs one call whether the queue holds one session or
forty.

Each span of prose it sends carries a stable id, and every candidate must cite
the ids it rests on. Before anything is stored:

- the cited spans must all be spans that were actually sent;
- the evidence quote must be verbatim, or near enough that only reflow and
  articles separate it from the span's own text. The deterministic lane accepts
  a looser paraphrase because its excerpts are the few sentences its gates
  already matched; this lane is shown thousands of words, where a partial
  overlap with some part of them is not evidence of anything;
- the claim itself must overlap that text on its content words, so a fluent
  invention riding a real quote is still rejected;
- a rationale that fails the same check is dropped rather than invented;
- a path the model names is kept only if the cited turns' tools touched it.
  Scope is resolved from those files, never from the model, and naming none is
  an honest repository-wide claim;
- a claim that bundles two independent choices is flagged for splitting at
  review, never split by machine.

Everything that survives is a **candidate**. Like the deterministic lane it is
written `proposed`, needs two distinct sessions to be proposed at all, and only
`repowise decision confirm` makes it govern. The model's own
`implemented_and_validated` verdict is evidence for review, not authority.

Zero calls, and a stated reason, on every path that should not call: capture
off, the source off, `llm: false`, no provider configured, and no new prose
since the last update. Prose that does not fit one update's budget stays queued
and is sent oldest-first next time, and a queued span is never aged out unread;
a provider failure requeues the same prose and retries it on the next two
updates before retiring it. Switching the source off leaves what is already
queued in place, to be drained if you switch it back on. New prose is only
captured while the source is on, so there is no backfill of what was read in
between. It also depends on the `session` source, which is what reads the
transcripts; with `session` off, discovery says so rather than reporting an
empty repository.

## Getting decisions back to your agent

Capture is half the loop. The other half is delivery, and it happens at two
moments without the agent asking (see [HOOKS.md](../agent/HOOKS.md)):

**At session start.** Repowise scores active decisions against the session's
likely working set (dirty and staged files, files changed on the branch versus
`main`, the previous session's edited files, branch-name tokens), expands that
one hop through import edges and co-change partners, and injects the top few
under a hard ~400-token cap. Relevance is multiplied by confidence and by
freshness, so a stale or low-confidence record has to be *much* more relevant to
make the cut. Nothing clears the floor means nothing is injected: decisions are
never shown just for being high-confidence.

**At edit time.** When the agent edits a file governed by a decision (through the
`file` and `module` node links), it gets a one-line notice with the rationale, at
most once per session per decision. This is the moment that matters, right before
the code is written.

Every injected decision id is recorded locally. On the next `repowise update` the
session miner checks whether the guidance was followed or contradicted by your
corrections in that session and stores that verdict on the injection, where
`repowise hook stats` reports the split. It deliberately does not touch the
record's staleness: whether you overrode a decision is a judgement about the
record, staleness is a measurement of the code, and one of those is per-machine
while the other travels with the repository.

Decisions also land in the generated `CLAUDE.md` (accepted records, freshest
first) and in `get_overview()`, `get_context()`, and the `governance_risk` flag
in `get_risk()` PR review.

Every one of those reads is the acceptance join, not the status column. A
candidate does not appear in `get_overview()`'s key decisions, cannot raise a
`governance_risk` directive against a pull request, and reaches `get_answer`'s
prompt labelled `candidate` rather than `active`. That labelling matters: the
row is rendered verbatim into a model prompt, and a candidate named `active`
reads to the model as something the repository has settled on.

## CLI reference

Every subcommand takes an optional trailing `PATH`; in workspace mode it targets
the primary repo. Decision ids accept an 8-character prefix.

| Command | What it does |
|---------|--------------|
Recording a decision from the UI follows the same rule as `repowise decision
add`: name the files or modules it governs and the write records an acceptance,
name none and it is stored as a candidate, and the form says which before you
press the button. Re-recording the title of a decision that is already accepted
without naming its scope is refused rather than applied, because it would clear
the scope that decision governs.

| `repowise decision add` | Guided interactive capture: title, context, decision, rationale, rejected alternatives, tradeoffs, affected files, tags. Answering the prompts is an acceptance, recorded as one. Name no files and it is kept as a candidate instead, because a decision that names nothing cannot be checked against the code. |
| `repowise decision list` | Table of id, title, status, source, confidence, staleness, created date. |
| `repowise decision show ID` | Full record including alternatives, consequences, affected files, and the evidence file and line. |
| `repowise decision confirm ID...` | Accept candidates. Refuses, naming the gap, when one has no reason, scope or evidence; `--reason`, `--scope` and `--evidence` supply them. A refused id does not stop the others. `--preview` writes nothing. |
| `repowise decision dismiss ID...` | Tombstone them. Never re-proposed on reindex. `--preview` writes nothing. |
| `repowise decision deprecate ID` | Retire it, optionally `--superseded-by <ID>`, which writes the lineage edge. |
| `repowise decision candidates` | What is awaiting review, and why each was raised. |
| `repowise decision merge ID INTO_ID` | Fold a candidate into an existing decision. |
| `repowise decision split ID` | Flag a candidate as bundling two choices. |
| `repowise decision export` / `import` | Round-trip accepted decisions through `.repowise/decisions.yaml`. |
| `repowise decision migrate` | Classify pre-split records. Dry run unless `--apply`. |
| `repowise decision health` | Counts, stale decisions, ungoverned hotspots, proposals awaiting review. |
| `repowise decision status` | What capture did: policy and preset, per-source state and why, review lanes and backlog age, staging queues, model spend. |
| `repowise decision config show` | The resolved capture policy: every source, its status, and why. |
| `repowise decision config preset NAME` | Apply `off`, `local_only`, `balanced`, or `full`. |
| `repowise decision source list` | The source registry with capabilities and current state. |
| `repowise decision source set SRC --on/--off` | Switch one source. `--llm/--no-llm` switches only its model stage. |
| `repowise decision llm --on/--off` | Master switch for decision-extraction model calls. |

`list` filters:

| Flag | Values |
|------|--------|
| `--status` | `proposed`, `active`, `deprecated`, `superseded`, `dismissed`, `all` (default) |
| `--source` | any source in the rank ladder except the retired ones, plus `all` (default) |
| `--proposed` | Shortcut for `--status proposed` |
| `--stale-only` | Only records with staleness at or above 0.5 |

Full flag reference: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-decision).

## The `get_why` MCP tool

`get_why(query=None, targets=None, repo=None)` dispatches into four modes:

| Call shape | Mode | Returns |
|------------|------|---------|
| No `query` | health | `counts`, `stale_decisions`, `proposed_awaiting_review`, `ungoverned_hotspots`, `conflicts` |
| `query` is a path | path | Three lanes: `decisions`, the accepted decisions governing that file (with `lineage` when the chain is longer than one); `candidates`, records naming it that nobody has accepted; and `history`, records accepted and since superseded or withdrawn. Plus an `origin_story` from git and an `alignment` read scored on the first lane only |
| `query` is a question | search | Ranked decision records plus `related_documentation`, optionally anchored with `targets` |
| `repo="all"` | workspace search | The same records across every workspace repo, each tagged with its alias |

The lanes never mix. `decisions` is what governs, and it is the only lane an
agent should read as a rule. `candidates` carries a `candidates_note` saying in
as many words that nobody has accepted them and how to accept one, and
`history` carries a `history_note` saying its records were accepted and have
since been replaced. When only candidates name a path, `alignment.score` is
`none` and its explanation says there is no accepted decision, rather than
reporting the file as governed. `alignment` also splits its counts four ways,
and they sum to `governing_count`: `active_count`, `deprecated_count`,
`uncheckable_count`, `candidate_count`.

Both non-governing lanes are capped at three rows and are the first things shed
under budget pressure: they are context, and a response that cannot afford the
rules must not spend on the queue. A note is dropped with its lane, so a
sentence never counts rows that are no longer in the payload.

It is designed never to come back empty-handed. If no accepted decision covers
a path, it falls back to git archaeology on that file. If git history is silent
too, it mines a rationale comment live from the source and returns it as
`code_rationale`. Semantic decision search falls back to full-text search when
the vector store is unavailable.

See [MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_why) for parameters and worked
examples.

## See also

- [INTELLIGENCE_LAYERS.md](INTELLIGENCE_LAYERS.md): where decisions sit among the five layers.
- [CODE_HEALTH.md](CODE_HEALTH.md): the `ungoverned_hotspot`, `stale_governance`, and `contradictory_decision` findings.
- [HOOKS.md](../agent/HOOKS.md): the SessionStart and edit-time injection hooks in detail.
- [CONFIG.md](../reference/CONFIG.md): the `decisions:` block.
