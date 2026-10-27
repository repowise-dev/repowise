# Refactoring intelligence: internals

User guide: [docs/layers/REFACTORING.md](../layers/REFACTORING.md). This page is for
contributors. Code lives in `packages/core/src/repowise/core/analysis/health/refactoring/`.

## Detectors

Each detector is one module registered with `@register` in `registry.py`; adding a
type is a new file plus that decorator. `detect_refactorings` runs every enabled
detector per file, isolates failures (a crashing detector yields no suggestion for
that file) and applies the `min_confidence` floor. Detectors run inside the health
pass (`HealthAnalyzer` in `analysis/health/engine.py`) on data already computed.

| Module | Method |
|--------|--------|
| `extract_class.py` | LCOM4 union-find components as candidate classes; god-class shape confirmed with WMC and TCC (Lanza-Marinescu). |
| `extract_method.py` | Intra-procedural dataflow (CFG, def/use, reaching definitions, liveness) over functions already flagged `large_method`, `brain_method` or `complex_method`. Spans must be single-exit and statement-bounded; the behavior-preservation gate suppresses what it cannot prove. |
| `extract_helper.py` | Rabin-Karp clone pairs, clustered transitively; the helper site is the community centroid of the involved files. |
| `move_method.py` | Jaccard distance between a method's entity set and each class; fires only when a foreign class is clearly nearer and draws more of its calls. |
| `break_cycle.py` | Import-graph SCCs (one repo-wide index per pass); greedy minimum feedback arc set picks the cut edges. |
| `split_file.py` | Community detection (Leiden via the shared graph helpers) over a weighted intra-file symbol graph of calls, shared helpers, shared imports and co-change; emitted only above a modularity gate. |
| `performance_fix.py` | Wraps causal performance opportunities that have a supported shared intervention. |

The methods follow published work: Fokaefs-Tsantalis class splitting, Bavota
feature-envy distance, MFAS for cycles, Newman modularity for decomposition.

## Extract Method gates

`extract_method.py` offers a span only when it can show the extraction keeps
behavior. Spans it cannot prove are dropped, so it under-reports by design.

- Behavior preservation: every returned value is written on every path, no
  state is carried across loop iterations, no local a closure shares crosses
  the span's edge, and there is no write to a `global` name without its
  declaration. A staged plan also leaves out a stage whose inputs or outputs
  depend on a `switch` or `match` arm's write.
- Error handling: a span or stage never changes which handler catches an error
  or what it sees. It is not offered when it would split a `try` from its
  handlers, write a value a handler or `finally` reads, or move a Go `defer`.
- Returns: a value the span writes is returned whenever any path can carry it
  to a later read, including past a later write in an `else` arm or a `try`
  body that may raise first.
- Refusals: spans too small to matter and spans that would carry the original
  finding into the helper are skipped. A component whose branching is mostly
  in its markup gets no extraction, and neither does a function that only
  fills one value field by field behind independent guards (summary rows,
  query parameters, optional settings), since splitting it simplifies neither
  half. The finding records why in `details.extract_method_refused`.
- Size: a span must remove at least two decision points, hold at least 8 code
  lines, cover at most 60% of the function's lines, and not start on the
  docstring. When the best span misses one of these, only a span that does not
  overlap it is offered, never a slightly smaller copy of it. Estimated gain
  never exceeds the share of decision points removed.
- Async: a span that awaits (Python `await`, `async with`, `async for`;
  TypeScript and JavaScript `await`, `for await`; Rust `.await`; C++
  `co_await`) carries `needs_async: true`: the helper is async and its call is
  awaited. An await inside a nested function, closure or Rust `async` block
  does not count. When the function holding the span is not declared async (a
  C++ coroutine), the step is a judgment call with reason
  `async_helper_unexpressible`.
- Receivers: the plan says under `new_symbol` whether the helper must be a
  method on the same object (the span uses `self`, `this` or a Go receiver)
  and which of the object's fields the span assigns directly. A span that uses
  `this` outside a class, or writes fields of a Go value receiver, is a
  judgment call (`receiver_unbound`, `receiver_copy_written`).
- Signatures: the plan writes out the new helper's header and the line that
  replaces the span, in the file's language, with parameter types where the
  code declares them. In TypeScript, an optional parameter is written
  `T | undefined`, and a type an earlier guard narrows (`if (!x) return;`) is
  written narrowed, or a note asks for the narrowed type. Plan detail shows
  both with a copy button. Where the code names no type, the text reads
  `<type>`.
- Staged plans: a Python, TypeScript or JavaScript function too complex for one
  helper can get several helpers the function calls in order, each small
  enough to leave the finding behind. Plan detail lists them as steps, with a
  shared parameter object when many values pass between them, and the plan's
  estimated gain is the whole drop in the function's complexity. The plan's
  own span, header and call are its first stage. When the work sits inside one
  loop or `if`, the stages are cut from its body and the function keeps the
  loop and calls them each time round. A stage running across several
  labelled sections is named after them. When a function moves between a
  single helper and a staged plan, or its stages change, the plan gets a new
  id.
- Anchors: every step, and every stage of a staged plan, shows the exact first
  and last lines of its span, so an agent finds it by its text after earlier
  stages have moved its lines; the line numbers are only a hint. The plan asks
  for each stage as a direct edit to the file, one at a time, never a script
  that rewrites it. A staged plan whose stages cannot all be pinned down that
  way falls back to a single helper, and a function with neither gets no plan.
- Batches: a staged plan with more than five stages comes in batches of at
  most five. Each batch names the tests to run before the next one, and a
  batch whose tests fail stops the plan there. The code runs after every
  batch, so a failure points at the stages just applied.
- Unfinished work: a plan that would still leave the function, or one of its
  new helpers, above the break-up bar says so, for example "leaves
  build_from_json at CCN 83, still above the break-up bar; more stages
  needed". The plan and its file's opportunity rank after the work that
  finishes the job. The mark is set when the repository is indexed, so a plan
  ranked live is not marked.

## Break Cycle imports

Cycles are found over runtime imports only. An import under
`if TYPE_CHECKING:` or a TypeScript `import type` / `typeof import()` is
type-only, and an import inside a function body is deferred (it runs on first
call, the usual way to break a cycle on purpose). Neither closes a cycle. Only
a plain `TYPE_CHECKING` or `module.TYPE_CHECKING` condition counts; a negated
or aliased flag is read as a runtime import.

## Test commands

A plan's test command runs from the owner of the tests it names:

| Language | Command |
|----------|---------|
| JavaScript, TypeScript | From the package whose `package.json` declares its `test` script (`npm --prefix ui-tui test -- src/a.test.ts`). A test no package can run gets no command. |
| Go | `go test ./internal/cart` |
| Rust | `cargo test -p parser` |
| Java, Kotlin | `./mvnw -pl core -am test` or `./gradlew :app:test` |
| Ruby | `bundle exec rspec` or `bundle exec rake test` |
| C# | `dotnet test` on the test project that holds it (`dotnet test tests/Shop.Tests/Shop.Tests.csproj`), narrowed with `--filter` to the named tests, or on the repository's solution when no single test project does. With neither, no command. |

A test whose build file or runner the repository does not have gets no
command.

## Plan shape

`RefactoringSuggestion` in `models.py` is the source of truth for every field
(`plan`, `evidence`, `impact_delta`, `effort_bucket`, `blast_radius`, `confidence`,
`source_biomarker`, `validation`). Text is rendered only at the surfaces.
`impact_delta` credits the share of the source finding the plan removes; for
Extract Method that is `ccn_removed / ccn`, `slice_nloc / nloc`, or the larger of
the two for `brain_method`, capped at 1. Extract Method's `helper_naming.py` gives
a helper the placeholder `extracted_helper`, numbered when the name is taken in
scope, and the plan asks the agent applying it to rename it.

Plan ids come from `identity.py`: a `refac<version>_` prefix over a per-type stable
kernel. Bumping the version re-mints ids; a held id from an older version reports
`stale_model`.

## Ranking

`recommendations.py` owns four components and one score:

```
priority = benefit * (1 + leverage) / (1 + cost + risk)
```

Benefit multiplies, so a plan with no evidence of gain scores zero. Leverage is
weighted health deficit, dependents and entry reach. Blast radius feeds cost and
risk, never benefit. Graph-native and performance plans take benefit from their own
evidence.

## Opportunities

`opportunity.py` composes a file's plans into one opportunity at index time; the
finalizer writes `refactoring_opportunities` and a `refactoring_summaries` row in the
same transaction that reconciles plans. `performance_fix` is excluded (the
performance layer owns those). Plans whose source finding the finding registry
withholds are left out of steps and evidence but stay addressable by id.

Plan rank and validation are worked out once per index, so plan lists and single
plans are served from SQL however many plans a repository has. An index written by
an older version keeps working, at the old speed, until its next update.

Serving (filters, views, scope, paging) lives in `serving.py`; the server reads it
through `services/refactoring_health.py`, which MCP `get_health` and the REST routes
share, so the two surfaces cannot disagree. Scope `fix_first` takes its eligibility
from the Fix-first builder itself.

## Counting

Every plan is counted once: the rollup's `plans_total` is the steps, plus
`evidence_total`, plus `unattached_plans_total` (plans in no opportunity, such
as a file whose only plan is a cycle). The plan list's `structural_total`
leaves Break Cycle out, matching the opportunities; `by_type` still counts it.

## REST routes (local server)

```text
GET  /api/repos/{repo_id}/refactoring/opportunities          ?view= &scope= &file_path= &limit=
GET  /api/repos/{repo_id}/refactoring/opportunities/{opportunity_id}
GET  /api/repos/{repo_id}/refactoring/summary
GET  /api/repos/{repo_id}/refactoring/targets                ?refactoring_type= &min_confidence=
GET  /api/repos/{repo_id}/refactoring/{suggestion_id}
POST /api/repos/{repo_id}/refactoring/{suggestion_id}/generate-code
GET  /api/repos/{repo_id}/refactoring/settings
PUT  /api/repos/{repo_id}/refactoring/settings
```

## Code generation

`llm/enrich.py` gathers source spans, builds the prompt, calls the provider, runs the
per-type self-check and caches results under the repo's `.repowise` directory by a
hash of plan, source and model. `llm_enrichment_enabled` treats an unset
`refactoring.llm.enabled` as off. The REST endpoint and MCP tool resolve the
provider with `get_chat_provider_instance` (`server/provider_config.py`), the
resolver chat uses; the CLI uses `resolve_provider`, as page generation does. The
MCP tool (`tool_refactoring.py`) is registered `default=False`.
