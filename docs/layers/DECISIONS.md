# Architectural Decisions

Repowise mines the *why* out of your repo: the ADRs, commit bodies, PR
descriptions, and `# WHY:` comments where your team already wrote down its
reasoning, plus the choices you make in coding-agent sessions. Every record is
tied to the files it governs, backed by a verbatim quote, tracked for staleness,
and pushed back at your agent at the moment it is about to violate or honor it.

## Quick start

```bash
repowise init                      # extraction runs as part of indexing
repowise decision list             # what has been captured
repowise decision list --proposed  # auto-proposed, awaiting your review
repowise decision confirm a1b2c3d4 # promote a proposal to active
repowise decision health           # stale, conflicting, ungoverned hotspots
repowise decision add              # guided interactive capture
```

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

From an agent:

```python
get_why(query="why JWT over sessions?")            # NL search
get_why(query="src/payments/processor.ts")         # what governs this file
get_why(query="why is caching split?", targets=["src/cache"])
get_why()                                          # health dashboard
```

## Where decisions come from

Eight capture sources run at index time. Seven are deterministic passes over the
repo and its git history; the eighth is a harvest during LLM doc generation, and
it is the only one that needs a provider.

| Source | Key | Reads | Notes |
|--------|-----|-------|-------|
| ADR files | `adr` | `adr/`, `adrs/`, `docs/adr/`, `docs/adrs/`, `docs/decisions/`, `decisions/`, `architecture/`, `doc/adr/` | Nygard and MADR headings plus YAML frontmatter, parsed without an LLM. Up to 60 files. `accepted`/`approved` map to `active`, `draft` to `proposed`, `rejected` to `deprecated`. |
| Inline markers | `inline_marker` | `# WHY:` / `# DECISION:` / `# TRADEOFF:` / `# ADR:` / `# RATIONALE:` / `# REJECTED:` | Any comment syntax (`#`, `//`, `--`, `/*`, `*`). Up to 5 continuation lines, plus 20 lines of surrounding context. Fenced code blocks in Markdown are skipped. |
| Git archaeology | `git_archaeology` | Commit messages | Gated on 19 decision verbs (migrate, switch to, replace, adopt, deprecate, drop, rewrite, split, revert, and the rest). |
| PR bodies | `pr` | Squash-merge and PR commit bodies | A body only qualifies when it looks like a PR description (`## Why`, `## Motivation`, `## Context`, `Closes #`, `Before:` / `After:`). Up to 25 bodies. |
| Code comments | `comment` | Block comments and docstrings on high-centrality files | Bounded to 30 nodes, and to prose carrying a rationale cue ("because", "instead of", "rather than", "trade-off", "we chose", "deliberately"). Centrality-bounded on purpose: comment archaeology across a whole repo is noise. |
| Doc generation | `llm_inferred` | The wiki generation pass | The page generator proposes decisions it inferred while writing a page. Every field must survive the grounding gate below. |

Two more sources sit outside the index-time set: `session` (mined from your
coding-agent transcripts, below) and `cli` (a decision you typed yourself, the
most authoritative source there is).

Turn any source off per repo in `.repowise/config.yaml`:

```yaml
decisions:
  session_mining: true
  sources:
    comment: false          # skip comment archaeology
    # inline_marker: false
    # git_archaeology: false
    # adr: false
    # pr: false
```

Sources you do not mention stay on, and unknown keys are ignored, so an old
config never breaks extraction. Full block reference:
[CONFIG.md](../reference/CONFIG.md).

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
3. **Observation-counted promotion.** A decision seen in two or more distinct
   sessions is promoted to `active` with `source: session`. A direct user
   correction promotes after one.

Everything stays on your machine. Transcripts are read locally, staging lives in
`.repowise/sessions/sessions.db`, and only the distilled decision text about the
codebase is stored. Turn the pipeline off with `decisions.session_mining: false`.

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

Decisions also land in the generated `CLAUDE.md` (active records, freshest
first) and in `get_overview()`, `get_context()`, and the `governance_risk` flag
in `get_risk()` PR review.

## CLI reference

Every subcommand takes an optional trailing `PATH`; in workspace mode it targets
the primary repo. Decision ids accept an 8-character prefix.

| Command | What it does |
|---------|--------------|
| `repowise decision add` | Guided interactive capture: title, context, decision, rationale, rejected alternatives, tradeoffs, affected files, tags. Stored `active` at confidence 1.0. |
| `repowise decision list` | Table of id, title, status, source, confidence, staleness, created date. |
| `repowise decision show ID` | Full record including alternatives, consequences, affected files, and the evidence file and line. |
| `repowise decision confirm ID` | Promote a proposal to `active`. |
| `repowise decision dismiss ID` | Tombstone it. Never re-proposed on reindex. |
| `repowise decision deprecate ID` | Mark deprecated, optionally `--superseded-by <ID>`. |
| `repowise decision health` | Counts, stale decisions, ungoverned hotspots, proposals awaiting review. |

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
| `query` is a path | path | The decisions governing that file (with `lineage` when the chain is longer than one), an `origin_story` from git, and an `alignment` read |
| `query` is a question | search | Ranked decision records plus `related_documentation`, optionally anchored with `targets` |
| `repo="all"` | workspace search | The same records across every workspace repo, each tagged with its alias |

It is designed never to come back empty-handed. If no decision record covers a
path, it falls back to git archaeology on that file. If git history is silent
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
