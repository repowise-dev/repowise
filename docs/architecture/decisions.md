# Decisions: capture internals

How the decision layer captures, verifies and ranks records. For what a user
does with them, see [layers/DECISIONS.md](../layers/DECISIONS.md).

Code lives under `packages/core/src/repowise/core/analysis/decisions/` (sources,
policy, lifecycle, provenance), `packages/core/src/repowise/core/sessions/`
(transcript mining) and `packages/cli/src/repowise/cli/commands/augment_cmd/`
(hook delivery).

## Source registry

`policy.py` defines one `SourceSpec` per source: whether it has a deterministic
stage, whether it has a model stage, its authority (`machine` or `human`) and
whether it ships on. One resolved policy backs the CLI, the REST API and the
index pipeline, so `repowise decision config show` prints what the next `init`
or `update` will run.

| Key | Deterministic stage | Model stage | Default |
|-----|---------------------|-------------|---------|
| `inline_marker` | yes | yes | on |
| `git_archaeology` | no | yes | on |
| `adr` | yes | yes | on |
| `pr` | no | yes | on |
| `comment` | no | yes | on |
| `session` | yes | yes | off |
| `session_discovery` | no | yes | off |
| `conventions` | yes | no | off |
| `cli` | yes | no | always (not switchable) |

A source with no deterministic stage reports `skipped_no_provider` when no
model is configured; a hybrid source falls back to its deterministic stage.
`session_discovery` and `conventions` were added after the presets existed, so
a stored preset that enumerates its sources does not silently gain them.

Source details:

- **ADR** (`adr.py`): files in a fixed set of ADR directories, up to 60. Nygard
  and MADR headings plus YAML frontmatter map to record fields. A status of
  `accepted` or `approved` maps to `active`. Self-acceptance happens in
  `persistence/crud/decision_ingest.py`: only `adr` is a tracked-artifact
  source, and only when git tracks the evidence file.
- **Inline markers** (`markers.py`): `WHY|DECISION|TRADEOFF|ADR|RATIONALE|REJECTED`
  followed by a colon, case-sensitive, after any comment leader. Up to 5
  continuation lines and 20 lines of context on each side. Fenced Markdown code is skipped.
- **Commits** (`commit_mining.py`, `commit_signals.py`): gated on decision
  verbs before any model call.
- **PR bodies**: only bodies with PR-description structure (`## Why`,
  `## Motivation`, `## Context`, `Closes #`, `Before:` / `After:`), up to 25.
- **Comments** (`rationale_comments.py`): block comments and docstrings on
  high-centrality files, bounded to 30 nodes, and only prose with a rationale
  cue ("because", "instead of", "trade-off", "we chose", "deliberately").
- **Conventions** (`conventions.py`): a wrapper of an I/O library that most
  files reach the library through while few import it directly. Go and Java
  are counted by package. Up to 10 per index.

## Evidence verification

Every produced field (`decision`, `rationale`, `source_quote`) is checked
against the verbatim source span the extractor recorded (`provenance.verify_quote`).

| Verdict | Fires when | Confidence multiplier |
|---------|-----------|-----------------------|
| `exact` | The normalized quote is a substring of the span | 1.0 |
| `fuzzy` | Token overlap with the span is at least 0.6 | 0.85 |
| `unverified` | Neither, or no span to check | 0.6 |

An ungrounded field is cleared. A candidate whose every produced field is
ungrounded is rejected. A candidate with no source text at all is kept but
stamped `unverified`.

## Confidence

`provenance.py` computes:

```
earned = 0.5 * (top_rank / 9) * (filled_fields / BODY_FIELDS)   # completeness scaling, when known
conf   = (0.4 + earned + min(0.12, 0.04 * (corroborating_sources - 1)))
         * verification multiplier
         * 0.85 if top_rank <= 2
clamped to [0, 0.99]
```

The rank ladder (`SOURCE_RANK`): `cli` 9, `session` 8, `adr` 7, `pr` 7,
`commit` / `git_archaeology` / `conventions` 6, `inline_marker` 4, `comment` 3.
Retired sources keep their rungs (`changelog` 5, `readme_mining` 3,
`code_comment` 2) so old rows still rank. `session` sits above `adr` because a
transcript carries what a person said while deciding; an ADR is a later
write-up. When two sources describe one decision, headline fields come from the
higher rank and the other is kept as a corroborating evidence row.

## Currency

`lifecycle.effective_currency` derives the shown currency from the stored one:
`superseded`, `dismissed` and an explicit `needs_review` are returned as set.
An `active` record becomes `uncheckable` with no scope, `stale` when every
named path is gone at HEAD (`head_artifacts.py`, renames followed, shallow
clones treated as present), `needs_review` at staleness 0.5 or above, and
`active` otherwise. Working agreements (`kind: agreement`) are repository-wide
and always read `active`. Only `active` and `needs_review` govern.

## Supersession and conflict detection

`evolution.detect_supersessions_and_conflicts` used embedding similarity to
scope candidate pairs, then opposing verbs or reversal phrases, with a model
tiebreaker. It is disabled (`SEMANTIC_SUPERSESSION_ENABLED = False`): among
records from one repository, high cosine similarity was the baseline, not
evidence of a shared topic. It was the only writer of `supersedes` and
`conflicts_with` edges. The diff-driven pass on `update` (`reverts.py`) still
marks a decision a commit reversed, and `deprecate --superseded-by` records the
successor on the record.

## Session mining

`sessions/miners/decisions.py` reads coding-agent transcripts through adapters
in `sessions/adapters/` (`claude_code`, `codex`). Only `claude_code` is read
unless `decisions.harnesses` names more. Codex stores sessions by date, so the
repository a line belongs to is decided by its recorded working directory;
sessions imported into Codex from another harness are skipped. Reading is
incremental from a cursor.

Three stages:

1. **Gates.** A user correction needs a pushback lead ("no,", "don't",
   "actually,", "instead"). An explicit choice needs a decision verb paired
   with a causal marker. A dead end needs three consecutive failures of the
   same command (`DEAD_END_FAILURES = 3`).
2. **Structuring.** One batched model call per update, capped at 60
   candidates (`MAX_STRUCTURED_PER_UPDATE`). Every field must quote the
   transcript; an ungrounded `source_quote` rejects the candidate.
3. **Promotion.** Seen in two distinct sessions, or one direct correction, the
   record is written as a `proposed` candidate with `source: session`. Repeat
   observations add evidence rows.

Staging lives in `.repowise/sessions/sessions.db`. Transcripts never leave the
machine; only distilled decision text is stored. The same switch gates
injected-decision feedback and update-time hook-efficacy replay.

### Broad discovery

`session_discovery` (`analysis/decisions/discovery/`) makes one model call per
update over newly read user and assistant prose, bounded by
`discovery.max_sessions` (1-24, default 12) and `discovery.max_input_tokens`
(2,000-60,000, default 30,000). It reuses the `session` source's read, so it
depends on that source being on. Each prose span carries an id; a candidate
must cite sent ids, quote near-verbatim, overlap the claim's content words,
and name only paths the cited turns' tools touched. Bundled claims are flagged
for splitting. Unsent prose stays queued oldest-first; a provider failure
requeues and retries on the next two updates.

## Review state and lanes

Candidates carry a review row (`open`, `accepted`, `merged`, `needs_split`,
`dismissed`) with the raising lane, extractor version, bundling flag, whether
any scope is named, and a priority equal to the acceptance check's verdict at
the last index. Re-extraction refreshes signals but never review state.
The five dashboard lanes are a join onto the acceptance log, not a filter on
status, so `GET /api/repos/{id}/decisions?lane=...` and `/decisions/lane-counts`
differ from `/decisions/counts`.

`decision status` keeps no run log: it derives its figures from records,
review rows, staging queues and `decision_extraction` cost rows, so spend is
all-time and a queue a store predates is reported absent.

## Hook delivery

`augment_cmd/decision_inject.py`: session start scores records against the
working set expanded one hop, multiplies relevance by confidence and freshness,
and injects under `_TOKEN_CAP = 400`, with candidates in a separate section
under `_CANDIDATE_TOKEN_CAP = 120`. Edit-time notices go through
`decision_node_links`, once per session per decision. Injected ids are recorded
in the sessions sidecar so the next `update` can tell whether guidance was
followed; that verdict does not change staleness.
