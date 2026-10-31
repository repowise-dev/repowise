# Savings accounting contract

How Repowise decides what it saved an agent, and what it refuses to claim.
Binding on every capture surface, the report, and anything that renders a total.

## Reporting vocabulary

- **Measured reduction** compares two observed artifacts on the same token scale.
- **Inferred avoidance** compares delivered input with a versioned, documented
  counterfactual. It is never presented as directly measured.
- **Observed opportunity** describes potentially avoidable activity. It is not a
  savings event and never enters achieved totals.
- **Metered spend** is provider-reported input/output usage for Repowise model
  work. It is reported separately from agent savings.
- **Unknown** means evidence was unavailable. It is not rewritten to a guessed
  agent, model, price, zero, or unsupported claim.

The achieved headline is `saved_input_tokens`. Measured and inferred composition
must be adjacent to it. If inferred tokens are nonzero, label the headline
`Estimated agent savings`. Output savings, when explicitly evidenced, remain a
separate dimension and do not inflate the input headline.

Current distill sizes use `chars_per_token_floor_v1`, defined as
`floor(serialized_characters / 4)`; MCP events use `chars_per_token_floor_v2`,
the same scale with the counterfactual table under "MCP counterfactuals". These are estimated tokens measured on a
stable local scale, not provider-billed tokenizer counts. Every event names its
unit and estimator; reports must not silently combine unlike units.

## Canonical dimensions and nulls

Every stored v1 event has a server-generated UUID `event_id`, an idempotency key,
UTC occurrence time, repository identity, surface, operation, evidence kind,
confidence, result state, an explicit `is_usable` flag, and all token-dimension keys. Token values are
nonnegative integers or `null`. `confidence` is a calibrated value from zero to
one for the event's savings evidence, or null when evidence is insufficient; its
meaning is versioned with the estimator.

- `baseline_input_tokens`: input that would have reached the agent without the
  evidenced Repowise operation.
- `pre_budget_input_tokens`: observed serialized input immediately before the
  adapter's final response budget. For distill/hook this equals the bounded raw
  baseline; for counterfactual MCP it can differ from the replaced-input estimate.
- `delivered_input_tokens`: final serialized input actually delivered, including
  recoverability markers and final metadata.
- `dropped_input_tokens`: observed input removed by the operation,
  `clamp(pre_budget_input_tokens - delivered_input_tokens)`. It is diagnostic
  when a counterfactual exists and is never added twice.
- `saved_input_tokens`: achieved input avoidance after the decision table below.
- `baseline_output_tokens`, `delivered_output_tokens`, `saved_output_tokens`:
  the equivalent output dimensions. They are `null` unless an adapter observes
  explicit output evidence; no fixed or estimated call-completion credit exists.
- `omission_refs`: zero or more content-recovery references. An empty list means
  no recovery artifact was linked, not zero savings.

`null` means unavailable or not observed, and a consumer must never read it as
zero. Attribution uses the literal `unknown` rather than null. Canonical fields
are serialized even when null, so an absent key never acquires a second meaning.

## Decision table

All subtraction is clamped. `clamp(x)` means `max(x, 0)`.

| Event/evidence | Required observed input | Canonical input formula | Evidence label | Counting rule |
|---|---|---|---|---|
| Distill or hook replacement | bounded raw input `B`; final delivered input `D` including marker | `dropped = clamp(B-D)`; `saved = dropped` | measured | one idempotent logical interaction |
| MCP with counterfactual | versioned replaced-input estimate `R`; final delivered input `D`; optional pre-budget `P` | `saved = clamp(R-D)`; if `P` exists, `dropped = clamp(P-D)` for diagnostics only | inferred | counterfactual wins only for the same `event_id`; its correlated drop is not added |
| MCP truncation without counterfactual | pre-budget response `P`; final delivered response `D` | `dropped = clamp(P-D)`; `saved = dropped` | measured | a different call to the same tool remains independently countable |
| Native VS Code or future adapter | the same explicit pair required by the applicable row above | use the applicable measured or inferred formula | measured/inferred as evidenced | no credit merely for invoking Repowise |
| Explicit avoided output | observed output baseline `OB`; observed delivered output `OD` | `saved_output = clamp(OB-OD)` | measured or versioned inferred | separate output total; never the input headline |
| Successful zero/negative delta | applicable evidence exists but baseline is not greater than delivered | `saved = 0` | retain evidence kind | counts as a successful call, not a saving interaction |
| Partial usable result | applicable evidence exists and final delivered content is usable; any omitted content is recoverable | same clamped formula as its event kind | retain evidence kind and `partial` state | reported separately from full success |
| Partial result without sufficient evidence | one or more required dimensions unavailable | `saved = 0`; unavailable dimensions are null | unknown coverage | never infer the missing side |
| Dead end or error | delivered overhead may be observed | `saved = 0`; overhead is diagnostic | retain evidence kind if known | not an answered query and never a negative saving |
| Unknown result state | insufficient evidence to prove success or useful partial result | `saved = 0` | unknown coverage | excluded from achieved savings |
| Opportunity observation | observed behavior/window only | no savings event and no achieved formula | opportunity | excluded from every achieved total |
| Retry/duplicate | same scoped idempotency key | keep one canonical event | unchanged | duplicates do not change tokens or counts |

An MCP query count is the number of unique successful or usable-partial MCP
interactions after idempotency, including a valid zero-saving result. Dead ends,
errors, unknown results, ledger rows, omission rows, and recovery blobs are not
queries answered. A saving-interaction count additionally requires
`saved_input_tokens > 0`.

`is_usable` describes whether the delivered result can be consumed by the agent;
it is independent of whether the baseline needed to calculate savings is known.
Success requires true, dead end/error/unknown require false, and partial may be
either. Query counts use result state plus `is_usable`, never token-field presence.

## MCP counterfactuals

`R` for an MCP call is what the agent would otherwise have read to get the same
answer. The formulas live in `server/mcp_server/_savings/counterfactual.py`; a
tool that holds the evidence (a file on disk, a scored diff) declares `R`
through them, and the dict-only estimator with flat floors is the fallback.
Events carrying this table record the estimator `chars_per_token_floor_v2`: the
same `floor(chars / 4)` scale as v1, with the baselines below. Events written
before it keep v1 and are not re-scored, because the ledger stores no payloads.

**A Read** is one rangeless Read of the file: its first 2,000 lines, which is
what Read returns without an offset or limit. When the caller is Claude Code
the count includes its line-number gutter (Read renders `cat -n`: a six-wide
number and a tab, seven characters per line). Other hosts read without one and
are counted on bare content. The Read-replacing hooks count the gutter on both
sides, since Claude Code numbers the replacement it is handed too.

| Tool | `R` | Why that is what it replaces |
|---|---|---|
| `get_symbol` | one Read of each file a served body came from | the agent opens the file to find and read the symbol |
| `get_context` file card, with or without skeleton | one Read of the file, once per file per call | the card answers what the file holds and how it is laid out, which is why the agent would open it |
| `get_context` module / symbol card | 600 per listed child file (cap 20,000) / 1,200 | a directory skim / locating a definition and its callers |
| `get_answer`, synthesised at high or medium confidence | 400 for the search plus one Read of each cited file | the answer stands in for finding and reading its sources |
| `get_answer`, low confidence or no provider | one Read per file whose whole verified body it inlined, plus 400 per other file it points at | a reply that says "verify" does not replace those reads; it replaces the search, as `search_codebase` credits, and any body it served, as `get_symbol` credits |
| `get_why` | 3,000 per call that serves evidence, plus 2,000 per file whose history it serves (cap 15,000) | searching decision records and commits for the question, and `git log -p` on each file: its median is 2,054 tokens across 23 httpx files and 3,089 across 16 files of this repository |
| `get_change_risk` | 500 plus 1,500 per changed file the score counted (cap 12,000) | reading the diff; declared from the scorer's own file count, which the default response does not show |

A baseline that comes out below the response it replaces saves zero; it is
never raised to make the tool look useful.

A reply that served nothing replaced nothing, so it has no `R` and no per-call
floor applies. Each tool decides that from its own reply:

| Tool | Served nothing when |
|---|---|
| any tool | the reply carries `resolved: false` (an unknown decision, evidence or finding id) |
| `get_why` | the reply has no `answer_basis` (no decision, episode, rationale, commit history, documentation or candidate was served); a reference lookup when `resolved` is false; the dashboard, which never has a basis, when every count is zero and every lane is empty |
| `get_risk` | every target is unresolved (not indexed, no such path) and the reply names no file that may break or missed a co-change |
| `get_health` | every named target is listed under `unresolved` and no file was measured |
| `get_change_risk` | `status` is `nothing_to_score` (an empty diff) |
| `get_blast_radius` | no requested target matched a service |
| `get_context`, `search_codebase`, `get_answer` | every target card is an error, the result list is empty, or the reply points at no file |

A reply that ran and found nothing to report, such as a dead-code sweep with no
findings or a conformance check with no violations, still answered the question
and keeps its floor.

## Correlation and attribution

MCP JSON-RPC request IDs are client-chosen and connection-scoped, so they are not
canonical event IDs. The server generates a UUID per invocation. Where available,
the adapter uses request ID plus a server/session namespace to form bounded,
hashed correlation evidence and an idempotency key; raw client IDs are not
persisted. The same generated `event_id` must reach response budgeting,
counterfactual estimation, dead-end handling, and the final writer.

FastMCP exposes a request ID across stdio, SSE, and streamable HTTP. On normal
stateful sessions it also exposes initialize-time `clientInfo`, which is
client-declared rather than authenticated. Mapping version `mcp_client_info_v2`
lowercases the name and removes non-alphanumerics, then looks the result up in
the agent registry (`repowise.core.agents.identity`), where every agent answers
to its own slug and its own display name, plus any extra name its host is known
to announce. Every other value, including a generic MCP client, maps to
`unknown`.

Resolving an announced name is the only step that produces `unknown`. Nowhere
else does savings enumerate agents: a stored id is checked syntactically against
`^[a-z0-9_]{1,32}$` by the writer, the reader, and the sidecar's own `CHECK`
constraints alike. So a new agent is a valid attribution the day its descriptor
lands, and an event from an agent since retired still reads back as that agent.

Never infer agent or model from headers, capabilities, environment variables,
executable parents, or transport. Session, request and tool-call IDs and model
stay null until an adapter supplies trustworthy evidence.

The v1 metadata allow-list is `client_info_normalized` and
`identity_mapping_version`. Values are bounded strings; no raw prompts,
transcripts, commands, headers, environment values, or secrets are accepted.

Native VS Code LM invocations generate an internal UUID at invocation entry and
carry it through REST work and final response bounding. The opaque VS Code
`toolInvocationToken` is not stringified or persisted. If server-side correlation
is later required, use an internal request header and request-local state; do not
expand public response models or use a process-global value. Delivered size is
measured after the VS Code host's final cap.

## Legacy omission fields

The legacy `omissions` table is a recovery store, not an event log. Its `ref` is
a content hash, so byte-identical omissions from two calls collapse into one row
and the row count cannot stand for a number of interactions.

- MCP budgeting persists the dropped recovery document or inline chunk.
  `original_tokens` is the estimated size of that persisted dropped artifact,
  including recovery-document framing where present; `kept_tokens` is always
  zero. Neither is whole-response before/after size.
- Distill persists the entire raw command artifact. `original_tokens` is that
  artifact's estimated size and `kept_tokens` is the retained filtered body
  before the recoverability marker. Actual delivered input includes the marker.

So both columns mean different things depending on who wrote them. Preserve them
and `evidence_references` for recovery, but keep them and the row count out of
savings arithmetic. Events link omission references without copying content.

## Pricing

Price each event only from the model, source, version, currency and rates
captured with that event, and apply each rate only to its matching dimension.
Reports expose priced and unpriced tokens separately and never reprice history
from the newest session. There is no fixed output-token credit; output fields
stay nullable so explicit evidence can be recorded without inventing any.

Repowise's own `llm_costs` spend is a separate metered-spend section, never
merged into or subtracted from achieved agent savings.

The rate is captured when the event is written, from a per-repository snapshot
cached in the sidecar (`pricing-snapshot.json`, 24-hour TTL). Detecting the
coding agent's model scans the local transcripts, which measures around six
seconds on a repository with no Codex history, so it cannot run per event.

**No agent-facing surface ever resolves it.** The MCP path, the rewrite hook
and the augment hook all read the cache and write an unpriced event when it is
cold. Each of them runs inside the agent's own tool call, where a six-second
stall is unacceptable even once a day, and the hooks are fresh processes that
could not amortize one anyway. The cache is filled by `repowise distill` run
directly and by `repowise saved` — a human typed both, and the second is asking
for the dollar figure.

So a repository that has only ever served MCP calls reports its savings as
unpriced until someone runs either command. That is a reported state, not a
gap: the report counts priced and unpriced tokens separately, and the surfaces
say which is which.

## One report, three consumers

`core/savings/service.load_report` is the only reader of the canonical ledger.
The savings endpoint, the repository overview headline and `repowise saved` all
map its `SavingsReport` into their own output and do no accounting arithmetic
of their own.

This is a correctness rule rather than tidiness. While the three aggregated the
ledger independently they disagreed four ways about the same repository:
`repowise saved` folded MCP counterfactual rows into its distill totals while
both endpoints excluded them; the endpoint's per-day series was drawn from a
different row set than its own summary, so the series did not sum to the total;
the dollar figure was computed three different ways, only one of which used an
output rate; and the overview ignored every time window. A parity test asserts
the endpoint and the core report agree.

A report of `None` means the repository has no sidecar, so nothing has been
measured. That is a different claim from a report of zero, which means it was
measured and was zero, and the surfaces render the two differently.

## Hosted and shared-presentation boundary

Hosted `total_cents` is the lifetime absolute value of negative
`credit_transactions.amount_cents` for one user and repository, partitioned by
`by_type` into indexing, chat, reindex and other. `transaction_count` counts
fetched ledger rows and can include non-debits, so it is not a count of charges.
Anonymous snapshot callers see zero billing spend.

Hosted credit spend therefore stays a named host-only billing section. It is not
equivalent to OSS `wiki.db.llm_costs` and is never folded into repository model
spend or savings.

The shared savings surface receives an already-derived presentation model: it
performs no pricing, correlation, or accounting of its own. Arithmetic lives
here, not in a renderer.

## Savings reset disclosure

Restarting savings history is a user-visible methodology change, not silent data
loss, so it is disclosed rather than absorbed. Incompatible estimates were
restarted on a stated date; omission history and other repository data were not
deleted, and saying so is part of the disclosure.

Disclosure is calm and inline beside the total: not a modal, warning, toast, or
promotional banner, and it never steals focus. It is dismissible once per
repository and accounting-method version, so a later methodology change can
announce itself again. After dismissal the reset date and a link to this document
stay quietly visible next to the number they qualify.

## Historical totals

No canonical pre-v1 savings total can be reconstructed. The legacy ledger has no
interaction ID and hash-deduplicates identical omitted content, so its rows
cannot be resolved back into logical interactions at any accuracy worth
publishing. Figures from the legacy algorithm are not a baseline to preserve or
to reproduce. Expected totals come from the deterministic fixture in
`tests/fixtures/savings/mixed_agents_v1.json`.
