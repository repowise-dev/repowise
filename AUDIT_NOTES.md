# Audit Notes — Codebase-Intelligence Fixes In Flight

This document tracks **proper fixes** that have been deferred in favour of
shippable, scoped patches. Each entry has enough context for a future
session (human or LLM) to pick it up cold.

Ordered by impact-per-hour, highest first.

---

## 1. Constructor-parameter type-use edges in the C# resolver

**Status:** deferred. Root cause behind most remaining DI false positives.

**Symptom (eShop, May 2026 audit):** even after PRs #164 and the
follow-up that skips `kind=interface` from unused-exports, classes
that exist solely to be DI-injected as concrete implementations still
read as orphans — e.g. `BasketMockService`, `CatalogMockService`,
`MauiNavigationService` (when registered via the single-arg
`AddSingleton<MauiNavigationService>()` form rather than the
two-arg `AddSingleton<INavigationService, MauiNavigationService>()`).

**Why the analyzer can't see them today:** the C# resolver emits
file-to-file `imports` edges from `using Foo.Bar;` directives. It
does **not** scan constructor signatures and emit a "uses-type" edge
from the constructor's class file to each parameter type's defining
file. So `public BasketViewModel(IBasketService basket)` produces
zero edges into `IBasketService.cs` from this file.

**Proper fix:** in
`packages/core/src/repowise/core/ingestion/resolvers/dotnet/`, after
namespace resolution, walk the AST for each parsed `.cs` file and
extract `(parameter_type, parameter_name)` pairs from every
constructor / primary constructor / method declaration. For each
parameter type that resolves to a known type-name in the project's
namespace map, emit an `imports` (or new `type_use`) edge with
confidence ~0.8.

**Estimated work:** 1.5–2 hours. Touch points:
- `packages/core/src/repowise/core/ingestion/queries/csharp.scm` —
  add a query capturing `parameter_list` children of `constructor_declaration`.
- `packages/core/src/repowise/core/ingestion/resolvers/dotnet/index.py` —
  resolve each captured type via the existing namespace map.
- New test fixture under `tests/unit/ingestion/test_csharp_resolver.py`
  asserting that `class A(IFoo foo)` produces an edge into the file
  defining `IFoo`.

**Languages affected:** same shape applies to Java, Kotlin, Scala
(also constructor-injection-heavy). Worth landing once and
parameterising.

---

## 2. XAML / Razor binding-path resolution

**Status:** deferred.

**Symptom:** MAUI/Xamarin model classes (`BasketItem`, `Address`,
`UserToken`, `Campaign`) and Blazor component code-behind are flagged
as unused exports when they're actually reached via XAML
`{Binding PropertyName}` paths or Razor `@PropertyName` references.

**Why:** the parser pipeline doesn't ingest `.xaml` / `.razor` as
code; the never-flag patterns added in PR #164 prevent the
code-behind files themselves from being flagged unreachable, but the
data-model classes those views bind to live elsewhere and have no
named import.

**Proper fix:** add a lightweight handler that, for each `.xaml` /
`.razor` file, regex-extracts `{Binding X.Y}` and `@Model.X` paths,
then emits `dynamic_uses` edges from the view file to any C# file
that defines a top-level type with a matching name. Belongs in
`packages/core/src/repowise/core/ingestion/dynamic_hints/dotnet.py`
or a new `xaml.py` extractor.

**Estimated work:** 2–3 hours including a fixture repo.

---

## 3. Minimal-API extension-method call resolution

**Status:** partially mitigated by never-flag patterns; proper fix
pending.

**Symptom:** `app.MapCatalogApi();` in `Program.cs` doesn't link back
to the file defining `public static IEndpointRouteBuilder
MapCatalogApi(this IEndpointRouteBuilder app)`. The current PR
papers over this with `*/Apis/*.cs`, `*/Endpoints/*.cs`,
`*/Routes/*.cs` never-flag globs — but that's path-shaped, not
behaviour-shaped, and any project that puts its endpoint modules
elsewhere (`src/Features/*/Endpoints.cs`, etc.) leaks back into
false positives.

**Proper fix:** during the import-resolution pass, build an index of
`public static \w+ Map[A-Z]\w+(this IEndpointRouteBuilder ...)` and
similar self-extension signatures. Then for each `.cs` file, scan
for `\.\s*Map[A-Z]\w+\s*\(` calls and emit a regular call edge to
the defining file. Same idea works for any extension method whose
first parameter is a host/builder type.

**Estimated work:** 1 hour.

---

## 4. Property usage via member access

**Status:** deferred.

**Symptom:** even with `kind=variable` (= C# auto-property) skipped
from `unused_exports`, `unused_internals` still flags private
auto-properties because no `calls` edge ever points at them.
`obj.Prop` is parsed as a member access expression, not a call, and
the resolver doesn't currently emit a "uses" edge for member access.

**Proper fix:** in the call-resolution pass, also resolve member-
access expressions to property symbols and emit a low-confidence
`calls` edge (or a new `reads` edge type). Languages affected: any
where attribute access is the canonical way to read state — C#,
Java, Kotlin, Scala, Swift, TS/JS, Python, Ruby.

**Caveat:** symbol-id collisions. `BasketItem.Id` and `OrderItem.Id`
share the unqualified name `Id`. The resolver needs to qualify by
parent class to emit correct edges. Reuse the same machinery that
already qualifies method calls.

**Estimated work:** 3–4 hours, mostly testing.

---

## 5. Co-change pair extraction returns 0 on real repos

**Status:** suspected algorithmic issue; not investigated.

**Symptom:** on eShop (1,081 commits across 538 files) and on the
local repowise repo (after the PR #164 update), every file's
`co_change_partners_json` is `[]`. No file has any co-change
partner. This is one of the four advertised intelligence layers and
it's completely empty on the repos we tested.

**Likely root causes (need to confirm):**
- The pair-detection algorithm requires more concurrent commits
  than the 90-day window provides for low-churn repos.
- The `min_count` threshold (default 3) is too strict for repos
  with sparse change history.
- The pair-extraction step is failing silently and returning empty
  results without an error.

**Proper fix:** instrument
`packages/core/src/repowise/core/ingestion/git_indexer.py` (or
wherever the co-change extraction lives — needs locating) to emit
counts at each filter stage. Lower default `min_count` to 2.
Consider expanding window to 365 days or last 500 commits, whichever
is larger.

**Estimated work:** 1–2 hours.

---

## 6. Hotspot definition is noise on stable repos

**Status:** known limitation; design choice rather than bug.

**Symptom:** `commit_count_90d` is the dominant signal in hotspot
scoring, so on a mature repo like eShop where the last 90 days have
6 total commits across 538 files, the "top hotspot" is effectively
"any file touched once in the last 3 months". The `Hotspots` table
in CLAUDE.md and the dashboard surface meaningless rankings.

**Proper fix:** trend-weighted scoring — exponential decay over the
full commit history rather than a hard 90d cliff. Already partially
implemented as `temporal_hotspot_score` in `git_metadata` schema
but not used as the primary ranking key in CLAUDE.md generation /
dashboard. Switch the ranking source.

**Touch points:**
- `packages/core/src/repowise/core/generation/editor_files/fetcher.py::_get_hotspots`
- the dashboard hotspot endpoint (web side).

**Estimated work:** 30 min.

---

## 7. Symbol metrics not exposed via `get_context`

**Status:** PR #164 populates `graph_nodes.pagerank` and
`betweenness` for symbol nodes. The web UI symbol detail panel reads
this directly. But `get_context(targets=[...], include=["metrics"])`
— the agent-facing tool — currently returns file-level metrics only.
Should also return symbol-level when targets include symbol IDs.

**Proper fix:** in
`packages/core/src/repowise/core/server/tools/get_context.py` (or
wherever the `include="metrics"` branch lives), check if each target
resolves to a symbol node and, if so, populate the metrics block
from the symbol's persisted columns.

**Estimated work:** 30 min.

---

## 8. Language-aware never-flag patterns

**Status:** the never-flag list in
`packages/core/src/repowise/core/analysis/dead_code/constants.py`
has accumulated a lot of .NET-specific globs. The list is checked
with `fnmatch` regardless of language, which is fine in practice
(none of the .NET globs collide with non-.NET paths) but the list
is becoming hard to scan.

**Proper fix:** organise patterns by language tag in a dict keyed by
`LanguageTag`. The analyzer can then ask the language registry
"which never-flag patterns apply to a file in this language?" and
fnmatch only those. Net structural cleanup; no behaviour change.

**Estimated work:** 45 min.

---

## 9. Tech-stack inference relies on filesystem manifests only

**Status:** fixed for the specific Node.js / .NET case in PR #164
(gates Node.js on real runtime evidence; adds .NET / EF Core /
Aspire / gRPC / MAUI detection). But the function still scans only
the repo root and one level deep. A monorepo with
`services/foo/foo.csproj` two levels down won't be detected.

**Proper fix:** instead of pattern-matching at fixed depths, query
the persisted `graph_nodes` table for the actual language
distribution and surface the top languages by share. The function
is currently filesystem-only because it runs before `graph_nodes`
exist on first init — but the tech-stack section in CLAUDE.md is
generated *after* graph build, so the data is available.

**Estimated work:** 1 hour. Touch points:
- `packages/core/src/repowise/core/generation/editor_files/tech_stack.py`
- `packages/core/src/repowise/core/generation/editor_files/fetcher.py`
- callers that pass a session along.

---

## 10. Skip `kind=interface` is broad — narrow it once ctor-param edges land

**Status:** introduced by the follow-up to PR #164 to unblock the
demo. Adding `interface` to `_NON_IMPORTABLE_SYMBOL_KINDS` is a
pragmatic over-correction: it prevents flagging any public interface,
including the rare case of a genuinely unused interface.

**Proper fix:** once item #1 (constructor-parameter type-use edges)
lands, interfaces will have real incoming edges from their consumers
and the universal skip can be narrowed to a language-aware skip
(C# / Java / Kotlin / Scala only). TS / Python / JS interfaces are
imported by name and the analyzer was already correct for them.

**Estimated work:** 15 min once #1 is in place.

---

## How this list got here

The May 2026 audit ran `repowise init` against `dotnet/eShop` and
the local repowise repo, queried the `wiki.db` directly, and
compared findings against the actual source. PRs #164 / #165 and
this follow-up address the immediate noise; everything above is the
work that wasn't safe to ship same-day.

When you pick one of these up, delete its entry from this file in
the same PR.
