# Refactoring intelligence (`repowise health --refactoring-targets`)

A health score tells you a file is in trouble. **Refactoring intelligence names
the specific fix.** Every other tool stops at the score, or prints the same
static sentence for every god class in every repo. repowise emits one structured
plan per opportunity: *split `GraphBuilder` into these three cohesive groups*,
*move `resolve_call` to the `resolvers` class where its calls actually land*,
*break the `pipeline ↔ update` import cycle by inverting this one edge*,
*decompose this 900-line module into these four files and rewrite the imports in
the six that depend on it*, computed deterministically from the same graph,
class model, and git data the score is built on.

```bash
repowise health --refactoring-targets            # ranked plans, biggest win for least effort
repowise health --refactoring-targets --format json
```

It runs **inside the health pass** (`init` / `update`), reusing data already
computed with no re-parse, **no LLM, no network**, inside the same <30s budget. The
LLM layer (code generation) is a separate, strictly opt-in step ([below](#opt-in-code-generation)).

<div align="center">
<img src="../../.github/assets/health-loop.svg" alt="repowise code-health loop: markers fan into three signals, the graph and git history locate risk, and refactoring intelligence emits concrete plans an agent executes" width="100%" />
</div>

## The seven detectors

Each detector is a self-contained module registered into a registry (adding a
refactoring type is a new file + a registry entry, like the marker registry).
A detector degrades to **"no suggestion" on any missing signal, never a wrong
one**, and produces stable-sorted, deterministic output.

| Type | What it names | Detection (deterministic) |
|------|---------------|---------------------------|
| **Extract Class** | The cohesion groups an incohesive / god class should split into: the exact methods + fields per group. | LCOM4 union-find components (each disconnected component is a candidate class), with the god-class shape confirmed via Lanza-Marinescu (WMC = Σ McCabe, TCC). |
| **Extract Helper** | A clone's exact occurrences and where the shared helper belongs. | Rabin–Karp clone pairs (line ranges, token count, co-change). The extraction site is the community centroid of the involved files; transitive clones (A↔B, B↔C) are clustered into one suggestion, not pairwise nags. |
| **Move Method** | A feature-envy method and the class it actually belongs to. | The method's entity set (fields/methods it touches, class-qualified) is built from the call graph; Jaccard distance to each class. Fires only when a foreign class is clearly nearer than its own. |
| **Break Cycle** | The minimal set of import edges to invert to break a dependency cycle. | A strongly-connected component in the import graph → greedy minimum feedback arc set (MFAS) over the real edges picks the smallest cut. |
| **Split File** | The cohesive files an oversized module should decompose into: which top-level symbols move to each new file, plus the import edits in every dependent. | Community detection (Leiden, Louvain fallback) over a weighted intra-file symbol graph (direct calls, shared local helpers, shared foreign modules); emits only when the partition's **modularity** clears a decomposability gate. The file-level analog of Extract Class. |
| **Extract Method** | The exact line span to lift out of an oversized/complex method, with the helper's inferred signature: the parameters it needs (IN) and the value it must return (OUT). | Intra-procedural dataflow (CFG + def/use + reaching definitions) over methods a `large_method` / `brain_method` / `complex_method` finding already flagged. Only single-exit, statement-boundary spans that remove real complexity qualify, and IN/OUT comes from liveness over the def/use facts. A span is offered only when the gate can prove it behavior-preserving: every returned value must be written on every path through the span, and a span nested in a loop must carry no state between iterations and must not mutate what the loop iterates. What the gate cannot prove is suppressed, not demoted, so the layer under-reports rather than suggest a rewrite that changes behavior. |
| **Performance Fix** | A safe shared intervention, affected call sites, and caller-to-sink paths for one causal performance opportunity. | The call-graph opportunity service groups raw findings by stable cause, boundary and context, then emits only supported strategies. Findings with no coherent safe intervention remain visible in Code Health without a plan. |

The algorithms are derived from public academic literature (Fokaefs-Tsantalis
HAC for class splitting, Bavota feature-envy distance, MFAS for cycle breaking,
Newman-Girvan modularity for module decomposition), not from any product.

**Split File is the cross-file wedge made concrete.** It is language-agnostic:
it reads only the already-built graph (`defines` / `calls` edges), so it works
the same on every language with call resolution, and it covers the gap LCOM4
leaves for Go (top-level functions, not class methods). Splitting Go files in the
same package is near-zero blast radius (no import edits); Python/TS get a
back-compat re-export shim, surfaced as `shim_required` on the plan.

## Anatomy of a suggestion

Every suggestion is **structured data, not a string**: the structure is the
source of truth; human-readable text is rendered only at the edges (CLI / MCP /
web).

| Field | Meaning |
|-------|---------|
| `refactoring_type` | `extract_class` \| `extract_helper` \| `move_method` \| `break_cycle` \| `split_file` \| `extract_method` \| `performance_fix` |
| `file_path`, `target_symbol`, `line_start`, `line_end` | What the refactoring acts on. |
| `plan` | The concrete, type-specific plan: the split `groups` (methods + fields), the move `{method, from_class, to_class}`, the clone `occurrences` + `suggested_site`, the cycle + `cut_edges`, the file-split `groups` (`{name, symbols, suggested_file}`) + `residual` core + `shim_required`, or the method-extraction `span` + `params` + `returns` + `suggested_name`. Names are omitted rather than invented: `suggested_name` and a group's `name` / `suggested_file` are `null` whenever no fact anchors a name, and `suggested_site.directory` is `null` unless an occurrence actually lives in that directory. |
| `evidence` | The signals that justify it: `lcom4`, `wmc`, clone token/line counts + `co_change_count`, Jaccard distances, cycle size, or the split's `modularity` + `symbol_count` + `group_count` + intra/cut edge counts. |
| `impact_delta` | The defect-health score the refactoring would recover; `0` for graph-native and performance plans. Their canonical `benefit` comes from detector-native evidence instead. |
| `effort_bucket` | `S` \| `M` \| `L` \| `XL`, from the target's size. |
| `blast_radius` | What else must move: the callers, co-change partners, and importing files. Extract Method carries `{"scope": "local"}` instead — extraction adds a private helper and changes no signature, so nothing outside the file moves and there is no count to make. |
| `confidence` | `low` \| `medium` \| `high` (drives the `min_confidence` surface gate). |
| `source_biomarker` | The finding this answers (e.g. `low_cohesion`, `god_class`, `dry_violation`). |

The per-type `plan` / `evidence` / `blast_radius` shapes are documented in full in
`packages/core/src/repowise/core/analysis/health/refactoring/models.py`.

## Ranking: graph-aware, not churn-only

Each detector sorts its own output, but the surfaces show one mixed list, so the
**global** order is what matters. The shared recommendation service owns four
named components and one canonical score:

```
score = (1 + benefit) * (1 + leverage) / (1 + cost + risk)
```

`benefit` is recoverable health or detector-native structural/performance gain;
`leverage` is weighted health deficit, dependents, and reliable entry reach;
`cost` includes effort and change surface; `risk` includes blast radius,
confidence, provenance, and validation quality. Larger blast radius therefore
cannot improve rank by masquerading as benefit. Performance plans keep their
detector-native benefit even with zero health impact. Ties break deterministically.

> **The wedge.** The leading commercial code-health tool ranks refactoring
> targets by **churn alone**, generates code **within-function only**, and ignores
> its own coupling signal at generation time. repowise ranks by graph centrality,
> works **across files** (class splits, method moves, cycle breaks), and feeds the
> co-change + graph context straight into the plan.

## Opportunities: the unit a surface serves

A **plan** is one detector output. An **opportunity** is one file's composed
refactoring: the diagnosis it leads with, its plans ordered so that applying
them in sequence is safe, and the clone groups that support the diagnosis
without instructing a change. The plan stays the addressable atom; a step names
one by `plan_id`.

Composition is deterministic and pure
(`analysis/health/refactoring/opportunity.py`), and it runs at **index time**:
the finalizer writes `refactoring_opportunities` and a one-row
`refactoring_summaries` headline in the same transaction that reconciles the
plans. Folding at read time was measured at 91 ms over 2,283 plans and 787 ms
at ten times that, plus 1,118 ms to rebuild the validation profiles, so every
page cost the whole repository.

| Field | Meaning |
|---|---|
| `opportunity_id` | `refop<model>_<digest>` over the member plan ids. Evidence is deliberately outside the kernel: a clone appearing or vanishing must not rename work an agent holds an id for. |
| `lead_biomarker` | The file's dominant finding, from the one owner of that rule (`analysis/health/models.py::primary_finding`). |
| `addresses_primary_problem` | Tri-state. `null` means no dominant finding was recorded, which is not the same claim as `false`. On the dogfood index it reads 201 true / 380 false / 3 unknown, and the false answers are the honest ones. |
| `steps[].applicability` | `mechanical` or `judgment`, with the reasons and the facts behind it, and the facts it could not establish under `unknowns`. Extraction is the only mechanical class. |
| `steps[].relocated_by` | An earlier step that moves this step's symbol to another file. The step's own `file_path` and span describe where the symbol *was*: locate it again before applying. Any surface that renders an ordered step list must say so. |
| `status` | Rolled up from the member plans' triage. Resolved only when every step is; one step marked a false positive does not resolve the rest. An `acknowledged` step is still outstanding work, so it keeps composing. |
| `steps[].finding_ids` | The findings this step's cause produced, so a step round-trips to its diagnosis through `get_health(finding_id=...)`. The key is **omitted entirely** when no finding on the file is addressable by id (a store written before findings carried public ids); an empty list means "none for this cause", which is a different answer. `lead_finding_ids` on the opportunity follows the same rule. |
| `steps[].validation_profile_id` | Points into the opportunity's `validation_profiles`. Resolved once at index time; the test-reachability walk behind it is never repeated per request. |

`performance_fix` plans are excluded by construction: the performance layer
composes, ranks and owns the lifecycle of its own opportunities.

### Ordering, and `refactoring_view`

The ranked head is honestly flat. On the dogfood index eight of the top ten
score identically: single high-confidence extractions recovering the same
quantised `complex_method` deduction, separated only by file path. They really
are equal under the published factors, so the answer is a queue that spends its
first rows on distinct problems rather than a tiebreaker invented to hide the
tie.

| `refactoring_view` | Order |
|---|---|
| `diversified` *(default)* | Rank order round-robined over (lead biomarker, lead refactoring type, containing area). Falls back to plain rank order when a repository has one cause in one area. |
| `canonical` | The published rank order verbatim, ties and all. What the old default produced. |
| `file_spread` | Asked for one row per file. An opportunity *is* one file's work, so the spread is satisfied by construction; the value resolves onto the diversified order, which is what it was reaching for. |

Both older values keep working. The same parameter also selects the legacy
`refactoring_plans` list's view, where `diversified` resolves to that list's
historical `canonical` default.

## Surfaces

```bash
repowise health --refactoring-targets            # ranked table
```

```python
# MCP. A bare call already carries one bounded refactoring_directive.
get_health()
get_health(include=["refactoring"], only=["refactoring_opportunities"], limit=6)
get_health(include=["refactoring"], only=["refactoring_summary"])
get_health(opportunity_id="refop2_...")                      # steps, plans, validation
get_health(opportunity_id="refop2_...", only=["refactoring_evidence"], cursor=3)
get_health(plan_id="refac2_...")                             # one plan, and its owner
get_health(include=["refactoring"], only=["refactoring_plans"])   # the raw list, opt-in
get_health(targets=["src/api/server.py"])                    # one file
```

```text
# REST. Both surfaces read services/refactoring_health.py, so they cannot
# answer differently; a parity suite asserts order, filters, totals and detail.
GET /api/repos/{repo_id}/refactoring/opportunities?view=diversified&limit=20
GET /api/repos/{repo_id}/refactoring/opportunities?file_path=src/api/server.py
GET /api/repos/{repo_id}/refactoring/opportunities/{opportunity_id}
GET /api/repos/{repo_id}/refactoring/summary
GET /api/repos/{repo_id}/refactoring/targets?refactoring_type=extract_class&min_confidence=high
GET /api/repos/{repo_id}/refactoring/targets/page?limit=60&offset=0&refactoring_type=performance_fix
GET /api/repos/{repo_id}/refactoring/{suggestion_id}        # one plan + blast-radius detail
```

A file's opportunities are a **separate call** - the queue filtered by
`file_path` - and deliberately not a field on the file-detail aggregate. That
aggregate is already the widest read in the server and every one of its callers
would pay for this whether or not it renders it, while the surface that wants
it knows it wants it. R2's `(repository_id, status, file_path)` index makes it
one indexed lookup rather than a scan.

The web **Refactoring** page uses one shared plan board and drawer for every
type, including a Performance filter. Its initial data path is bounded and
server-owned: search, type, confidence, effort, canonical sorting, true totals,
and deterministic offset cursors. The older unpaged endpoint remains available
during migration. The drawer explains benefit, leverage, cost, risk and rank;
shows validation basis and provenance, true test totals, capped tests and
commands; and makes affected paths and intervention points navigable. Each card
can still export the structured plan for an agent. It never runs tests, creates
edits, or applies a refactoring automatically.

## Optional code generation

The deterministic plan is the product. The LLM code-generation step is **never
in the indexing hot path**: it runs only when you ask for code for a specific
plan, and it is on by default. Configure it in `.repowise/config.yaml`:

```yaml
refactoring:
  enabled: true
  detectors:
    disabled: []              # e.g. [move_method]
  min_confidence: medium      # low | medium | high
  llm:
    enabled: true             # on by default; set false to disable
    provider: null            # falls back to the repo's configured provider
    model: null
```

When code generation is enabled (the default), the **Generate code** action on a plan card (or the
endpoint below) gathers the plan's real source spans off the working tree, builds
a behavior-preservation prompt carrying the structured plan **plus the
graph/co-change context** a bare codegen tool throws away, and returns the
refactored code and a unified diff. Where a self-check is cheap and meaningful it
runs one: Extract Class re-walks the generated classes for an **LCOM4 before/after
delta**, and Split File re-walks the generated files to assert each is **below the
size floor** and the symbols are **partitioned with no duplication**. Results are
cached on disk by a content hash (plan + source + model), so the same plan never
pays twice.

```text
POST /api/repos/{repo_id}/refactoring/{suggestion_id}/generate-code
GET  /api/repos/{repo_id}/refactoring/settings        # read llm.enabled + provider/model
PUT  /api/repos/{repo_id}/refactoring/settings        # toggle it from the dashboard
```

Code generation needs the working tree on disk (it reads the real source spans),
so it is a local-`serve` capability: it returns `403` when disabled and `404`
when the repo has no accessible checkout. **Apply is out of scope**: the wedge is
the plan and the reviewable diff, not auto-applied edits.

## Configuration

Per-path disables reuse the existing `.repowise/health-rules.json` glob
mechanism, so a refactoring type can be silenced for generated or vendored paths
the same way a marker is:

```json
{
  "rules": [
    { "path": "src/generated/**", "disabled_biomarkers": ["dry_violation"] }
  ]
}
```

## See also

- [`docs/layers/CODE_HEALTH.md`](CODE_HEALTH.md): the markers and the three health
  signals the suggestions are built on.
- [`docs/layers/INTELLIGENCE_LAYERS.md`](INTELLIGENCE_LAYERS.md): how code health fits
  the five-layer index.
- [`docs/agent/MCP_TOOLS.md`](../agent/MCP_TOOLS.md): the `get_health(include=["refactoring"])`
  response shape.
