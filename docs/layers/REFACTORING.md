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
| **Extract Method** | The exact line span to lift out of an oversized/complex method, with the helper's inferred signature: the parameters it needs (IN) and the value it must return (OUT). | Intra-procedural dataflow (CFG + def/use + reaching definitions) over methods a `large_method` / `brain_method` / `complex_method` finding already flagged. Only single-exit, statement-boundary spans that remove real complexity qualify; IN/OUT comes from liveness over the def/use facts, so the suggested helper is behavior-preserving by construction. |
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
| `plan` | The concrete, type-specific plan: the split `groups` (methods + fields), the move `{method, from_class, to_class}`, the clone `occurrences` + `suggested_site`, the cycle + `cut_edges`, the file-split `groups` (`{name, symbols, suggested_file}`) + `residual` core + `shim_required`, or the method-extraction `span` + `params` + `returns` + `suggested_name`. |
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

## Surfaces

```bash
repowise health --refactoring-targets            # ranked table
```

```python
# MCP: the same structured plans, ranked
get_health(include=["refactoring"])
get_health(targets=["src/api/server.py"])        # one file
get_health(targets=["module:src.api"])           # one module
```

```text
# REST: what the web tab reads
GET /api/repos/{repo_id}/refactoring/targets?refactoring_type=extract_class&min_confidence=high
GET /api/repos/{repo_id}/refactoring/targets/page?limit=60&offset=0&refactoring_type=performance_fix
GET /api/repos/{repo_id}/refactoring/{suggestion_id}        # one plan + blast-radius detail
```

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
