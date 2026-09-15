# Savings accounting contract

Status: Phase 0 decision record for the costs-and-savings overhaul. This document
locks the v1 accounting truth before schema, capture, reporting, or UI work begins.

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

Current distill and MCP sizes use `chars_per_token_floor_v1`, defined as
`floor(serialized_characters / 4)`. These are estimated tokens measured on a
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

`null` means unavailable or not observed. Applicability comes from surface,
evidence kind, and result state; consumers must not infer zero from `null`.
Enum attribution uses the literal `unknown` instead of null. Optional correlation,
model, and pricing fields use null when unavailable. Canonical serialized fields
are present even when null, so field omission never acquires a second meaning.

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

## Correlation and attribution

MCP JSON-RPC request IDs are client-chosen and connection-scoped, so they are not
canonical event IDs. The server generates a UUID per invocation. Where available,
the adapter uses request ID plus a server/session namespace to form bounded,
hashed correlation evidence and an idempotency key; raw client IDs are not
persisted. The same generated `event_id` must reach response budgeting,
counterfactual estimation, dead-end handling, and the final writer.

FastMCP exposes a request ID across stdio, SSE, and streamable HTTP. On normal
stateful sessions it also exposes initialize-time `clientInfo`, which is
client-declared rather than authenticated. Mapping version `mcp_client_info_v1`
lowercases the name and removes non-alphanumerics, then accepts only:
`claude`/`claudecode` -> `claude_code`, `codex` -> `codex`, `opencode` ->
`opencode`, `hermes` -> `hermes`, `cursor` -> `cursor`, and `vscode` ->
`vscode`. Every other value, including a generic MCP client, maps to `unknown`.
Never infer agent or
model from headers, capabilities, environment variables, executable parents, or
transport. Session/request/tool-call IDs and model remain null until an adapter
explicitly supplies trustworthy evidence.

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

The legacy `omissions` table is a recovery store, not an accounting event log.
Its content hash `ref` identifies content, so byte-identical omissions from two
calls collapse to one row. Its row count cannot represent logical interactions.

- MCP budgeting persists the dropped recovery document or inline chunk.
  `original_tokens` is the estimated size of that persisted dropped artifact,
  including recovery-document framing where present; `kept_tokens` is always
  zero. Neither is whole-response before/after size.
- Distill persists the entire raw command artifact. `original_tokens` is that
  artifact's estimated size and `kept_tokens` is the retained filtered body
  before the recoverability marker. Actual delivered input includes the marker.

Therefore the columns have producer-specific meanings. Preserve them and
`evidence_references` for expand/recovery, but do not use either column or the
omission row count in canonical savings arithmetic. New events link omission
references without duplicating their content.

## Pricing

Price each event only from the model, source/version, currency, and input/output
rates captured with that event. Savings reports expose priced and unpriced tokens
separately. They never reprice history from the newest session. Input and output
rates apply only to their matching dimensions. The old fixed 60-output-token
credit is removed; nullable output fields remain so explicit evidence can be
supported without invention.

Repowise's own `llm_costs` provider-token spend is a separate metered-spend
section. It is not subtracted from or merged into achieved agent-token savings.

## Hosted and shared-presentation boundary

The verified hosted backend defines `total_cents` as the lifetime absolute value
of negative `credit_transactions.amount_cents`, scoped to authenticated user and
repository. `by_type` partitions those debits into indexing, chat, reindex, and
other, so its money buckets sum to `total_cents`; positive/zero rows do not add
spend. The current `transaction_count` is the number of fetched ledger rows and
can include non-debits. Anonymous public-snapshot callers receive zero billing
spend. Hosted engine `llm_costs` telemetry is a separate field.

Accordingly, hosted credit spend remains a named host-only billing section. It is
not equivalent to OSS `wiki.db.llm_costs` and is not mapped into canonical
repository model spend or savings.

`frontend/docs/DESIGN_LANGUAGE.md` remains binding for hosted and OSS. The shared
`@repowise-dev/ui` savings surface receives a pure, already-derived presentation
model plus link/action capabilities. It performs no fetching, authentication,
routing, pricing, correlation, or accounting and contains no `isHosted` branch.
Hosts adapt their real wire contracts and retain hosted-only billing as a sibling
capability. Visual hierarchy, evidence labels, and interaction semantics stay
shared.

## Savings reset disclosure

The clean telemetry reset is a user-visible methodology change, not silent data
loss. On the first costs-page visit after the reset, show a calm inline
informational notice beside the savings lede:

> **Savings accounting has been upgraded**
> Savings now use interaction-level evidence for more accurate, auditable totals.
> Previous savings estimates were not compatible with the new methodology, so
> savings history restarted on **[reset date]**. Omission history and other
> repository data were not deleted.

The notice has a normal `See what changed` link and a dismiss control. It is not a
modal, warning, error, toast, or global promotional banner: it must not steal
focus or interrupt work. After dismissal, the reset date and methodology link
remain quietly visible near the total.

Presentation is a reusable controlled `DismissibleNotice` primitive in
`@repowise-dev/ui`. It accepts content, neutral/info tone, optional action/link,
and `onDismiss`; it owns no storage, fetching, routes, or host detection. Each host
decides whether it is visible and persists a versioned dismissal key scoped to
repository plus accounting-method version. This keeps localStorage, user
preferences, and future server persistence interchangeable without turning a rare
announcement into a general modal framework. Existing feature-specific banners
may migrate only when doing so is independently useful, not as required scope for
this overhaul.

## Frozen diagnostic baseline

On 2026-09-13 a SQLite online backup of the worktree-local omission database at
commit `a3e49b7692be` was frozen, queried, and deleted. Its SHA-256 was
`23E44B9E4C16A29087D5A9C2FB4F5EACF366C16E126281D44CA9B135941A5298`.
It contained 953 MCP ledger rows and 36 MCP omission artifacts. The legacy
tool-wide algorithm reported 7,495,645 MCP tokens; blindly adding both signal
sets produced 7,959,262. The clamped non-MCP ledger reduction was 1,276,569.

These values are diagnostic reproduction evidence only. No canonical historical
MCP total can be reconstructed because there is no interaction ID and identical
omitted content is hash-deduplicated. Product expectations come exclusively from
the deterministic fixture in `tests/fixtures/savings/mixed_agents_v1.json`.
