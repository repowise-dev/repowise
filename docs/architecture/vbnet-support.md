# VB.NET Language Support — Architecture Decisions & Implementation Plan

> Status: **Implemented** (2026-07-23) · Target tier: **Good** (not Full this
> pass) · Author decisions captured 2026-07-23. Companion to
> [architecture/language-support.md](language-support.md) (how the pipeline works)
> and [layers/LANGUAGE_SUPPORT.md](../layers/LANGUAGE_SUPPORT.md) (user-facing matrix).

This document records **why** VB.NET support is built the way it is (the
architectural decisions and their trade-offs) and **what** the concrete
implementation steps are. It is an ADR + delivery plan in one file.

---

## 1. Context

VB.NET is a first-class .NET language that compiles into the same
MSBuild / assembly / namespace world as C#. repowise already has deep .NET
infrastructure built for C#:

- `resolvers/dotnet/` — `DotNetProjectIndex` parsing `.csproj`/`.sln`, a
  namespace→file map, the MSBuild project graph, and global/implicit usings.
- `framework_edges/aspnet.py` — ASP.NET route/controller and EF Core
  `DbContext`/`DbSet<T>` edges.
- `dynamic_hints/dotnet.py`, `extractors/synthetic_symbols/csharp_mvvm.py`,
  `languages/csharp_member_reads.py`, `languages/csharp_same_namespace.py`.

The goal is to reuse this .NET machinery where the languages genuinely overlap,
while isolating the places VB.NET diverges from C# (syntax, import model,
namespace semantics).

---

## 2. Decisions

Each decision lists the choice, the alternatives considered, and the rationale.

### D1 — Target the AST tier via `tree-sitter-vbnet`

**Decision:** Parse VB.NET to a real AST using the `tree-sitter-vbnet` PyPI
grammar, rather than the regex/lightweight tier used for F#.

**Evidence gathered:**
- `tree-sitter-vbnet` **0.1.0** exists on PyPI (repo:
  `github.com/rrangraj/tree-sitter-vb-dotnet`), `requires-python >=3.10`,
  depends on `tree-sitter~=0.24` — inside repowise's `tree-sitter>=0.23,<1` pin.
- Verified live: it installs, loads via `tree_sitter_vbnet.language()`, and
  parses `Imports`, `Namespace`, `Class`, `Module`, `Sub`/`Function`,
  constructors, and calls into a usable tree.

**Alternatives:**
- *Lightweight/regex tier (like F#)* — lower risk, but no symbols/calls/heritage.
  Rejected because a working grammar exists and AST unlocks the full graph.
- *Vendor/build the CodeAnt grammar from source* — more control, but adds a
  non-PyPI build step. Unnecessary given the PyPI package works.

**Consequence:** VB.NET gets AST symbol extraction, import resolution, and call
edges. See D3 for the heritage caveat that keeps the tier at **Good**, not Full.

### D2 — Platform-gated, optional grammar dependency

**Decision:** Add `tree-sitter-vbnet` as an **optional, `sys_platform == 'win32'`
gated** dependency; the pipeline degrades `.vb` files to passthrough where the
grammar module is not importable.

**Why:** The only published wheel is `cp310-abi3-win_amd64` — **Windows-only,
no sdist, no Linux/macOS wheels**. A hard dependency would break installs and CI
on non-Windows platforms.

**Consequence / known asymmetry:** VB.NET gets full AST coverage on Windows and
**passthrough (git history + wiki only) on Linux/macOS** until upstream ships
cross-platform wheels. This is an accepted, documented limitation.

**Note on degrade depth:** `lightweight_imports/__init__.py` has no `"vbnet"`
entry, so the non-Windows degrade path returns zero imports — one tier below
F#'s regex-based partial import graph, not just "no AST." Decision: accept
this as pure passthrough for this pass rather than building a regex-tier
fallback; document it plainly in the Roadmap entry (§Docs) so it isn't
mistaken for F#-parity.

**Precedent note:** no currently-shipped grammar dependency in `pyproject.toml`
is platform-gated (`sys_platform` markers today only appear in transitive
deps inside `uv.lock`, e.g. `pywin32`) — the degrade-to-passthrough path in
`parser.py` is real and already handles `ImportError` generically, but no
language currently exercises it in production. VB.NET is the first real test
of that path end-to-end.

### D3 — Heritage via regex fallback (stopgap)

**Decision:** Extract `Inherits` / `Implements` relationships with a targeted
regex over the class-body source text, feeding the normal `HeritageRelation`
pipeline — **not** from the AST.

**Why:** The v0.1.0 grammar **fails to parse `Inherits`/`Implements`** — it emits
an `ERROR` node for those clauses (verified live on a valid sample). Everything
else (classes, modules, members, imports) parses cleanly.

**Alternatives:**
- *Ship without heritage* — honest but loses inheritance edges entirely.
- *Wait for the grammar* — blocks the whole feature on upstream.

**Consequence:** Heritage works now, but via a fragile text scan. Tracked as a
TODO to switch to AST extraction once the grammar supports these clauses. This
limitation is the main reason the tier is **Good**, not Full.

**Verification caveat:** `tree-sitter-vbnet` 0.1.0 declares `tree-sitter~=0.24`
(i.e. `>=0.24,<0.25`) as its dependency, but the repo's actual locked version
(`uv.lock`) is `tree-sitter==0.25.2` — outside that declared range. The "parses
cleanly" claim above must be re-verified live against 0.25.2 specifically
before Phase 2 lands; if `tree_sitter_vbnet.language()` fails to load under
0.25.2, D1 needs revisiting (e.g. a `tree-sitter` version shim/isolation for
this one grammar, or pinning down to confirm actual compatibility despite the
metadata mismatch).

### D4 — Reuse the shared .NET project index

**Decision:** Extend `resolvers/dotnet/` (not fork it) to understand `.vbproj`
and `.vb`, and resolve VB `Imports` against the same namespace→file map C# uses.

**Why:** `.vbproj` is the *same* MSBuild XML schema as `.csproj`; the project
graph, `ProjectReference`, `PackageReference`, and `RootNamespace` handling are
identical. Forking would duplicate a large, well-tested subsystem.

**Two VB-specific semantics that must be handled explicitly:**
1. **Project-level root imports** — VB declares implicit imports once in the
   project file via `<Import Include="System.Linq"/>` (an `ItemGroup` item),
   whereas C# uses `<Using Include="..."/>`. The parser must recognise both.
2. **`RootNamespace` is prepended to *every* declaration** in VB. Namespace
   resolution must account for this or cross-file lookups will miss.

   **Correction to original framing:** this is *not* an extension of existing
   C# behaviour — `MSBuildProject.root_namespace` is already parsed
   (`msbuild.py`) but is not consumed anywhere in `namespace_map.py` today; C#
   namespace resolution is purely a regex scan of explicit `namespace` blocks
   and never prepends `RootNamespace`, even for files without one. Building
   VB's root-namespace prepending is therefore new logic, not a generalisation
   of a working conditional. **Decision: scope this to VB.NET only** — do not
   change C#'s namespace resolution behaviour as part of this pass, to avoid
   regression risk on a working path outside this feature's scope.

### D5 — Include ASP.NET + EF Core framework edges

**Decision:** Add VB-syntax variants of the ASP.NET and EF Core detectors and
lift the `language != "csharp"` gate in `framework_edges/aspnet.py`.

**Why it is not free reuse:** The detectors are C#-syntax regexes and are gated
to C# files. VB expresses the same concepts differently:

| Concept | C# | VB.NET |
|---------|----|--------|
| Attribute | `[ApiController]` / `[HttpGet]` | `<ApiController>` / `<HttpGet>` |
| Inheritance | `class X : DbContext` | `Inherits DbContext` |
| Generic | `DbSet<T>` | `DbSet(Of T)` |

**Trade-off acknowledged:** These edges only fire on modern ASP.NET Core /
EF Core code. Classic VB.NET (WinForms, WebForms, VB6-migrated business logic)
triggers none of them. Included this pass per product decision.

### D6 — Health markers deferred to a follow-up

**Decision:** Do **not** add the code-health complexity walker map or perf
dialect for VB.NET in this pass; schedule as phase 2.

**Why:** Health markers are self-contained and additive (they degrade to
silence), independent of the graph pipeline, and gate the Full-tier badge. They
can land in a separate PR without blocking symbols/imports/heritage.

**Consequence:** VB.NET is **Good** tier until D3 (AST heritage) and D6 (health
markers) both land, at which point it can be promoted to Full.

---

## 3. Implementation steps

Ordered phases. Each item names the file(s) and mirrors the closest C# analog.
Paths below are given relative to the `ingestion/` package root, i.e.
`packages/core/src/repowise/core/ingestion/...`.

**Note on `specs/__init__.py`:** `ALL_SPECS` is grouped under section comments
(e.g. "Traversal-tier languages (scaffolded — grammar not yet wired)" over C#
and Scala, "Extra languages — git blame coverage only" over F#) that are stale
relative to reality — C#/Scala have full wiring and F# has a real lightweight
resolver. Don't take those comments as a signal of where to slot `_VBNET`;
place it near `_CSHARP` per the plan, and treat the section headers as
pre-existing drift outside this feature's scope (optionally worth a cleanup
comment fix while touching this file, not a functional blocker).

### Phase 1 — Language identity & wiring
1. **`ingestion/languages/specs/vbnet.py`** — new `SPEC` mirroring
   `specs/csharp.py`: `tag="vbnet"`, `display_name="VB.NET"`,
   `extensions={".vb"}`, `grammar_package="tree_sitter_vbnet"`,
   `scm_file="vbnet.scm"`, `import_support="full"`,
   `heritage_node_types={"class_block","interface_block","structure_block","module_block"}`,
   entry points (`Program.vb`, `ApplicationEvents.vb`), manifests
   (shared with C#), generated suffixes (`.Designer.vb`, `.g.vb`),
   `blocked_dirs=("bin","obj",".vs","TestResults","packages")`, VB
   builtins/parents, `color_hex="#945db7"`.
2. **`ingestion/languages/specs/__init__.py`** — import `_VBNET`, slot into
   `ALL_SPECS` near `_CSHARP` (no `.cs`/`.vb` extension clash, so order is not
   sensitive here).
3. **`ingestion/models.py`** — add `"vbnet"` to the `LanguageTag` literal.
4. **`pyproject.toml`** — add
   `tree-sitter-vbnet>=0.1,<1 ; sys_platform == 'win32'`; ensure grammar
   loading degrades to passthrough when the module is missing (D2).

### Phase 2 — AST extraction
5. **`ingestion/queries/vbnet.scm`** — tree-sitter query against the grammar's
   actual node names: `imports_statement`/`namespace_name` → imports;
   `class_block`/`module_block`/`structure_block`/`interface_block` → symbols;
   `method_declaration`/`constructor_declaration`/property/event members;
   `invocation_expression` → calls.
6. **`ingestion/language_configs.py`** — `"vbnet"` `LanguageConfig` mirroring
   C#'s: symbol node types, `import_node_types=["imports_statement"]`,
   visibility fn, parent extraction, entry points.
7. **`ingestion/extractors/bindings/vbnet.py`** + register in
   `bindings/__init__.py::_DISPATCH` — parse `Imports X.Y` and aliased
   `Imports Alias = X.Y`.

### Phase 3 — Heritage (regex fallback — D3)
8. **`ingestion/extractors/heritage/vbnet.py`** + register in
   `HERITAGE_EXTRACTORS` — regex-extract `Inherits`/`Implements` from the class
   body, emit `HeritageRelation`s. Documented stopgap with a TODO to move to AST.

### Phase 4 — Import resolution (shared .NET index — D4)
9. **`ingestion/resolvers/dotnet/msbuild.py`** — `find_vbproj_files`; teach the
   parser to read `.vbproj` and VB `<Import Include>` root-imports; generalise
   `MSBuildProject` naming away from "csproj".
9a. **`ingestion/resolvers/dotnet/solution.py`** (added — missing from the
    original plan) — `solution.py` currently hard-filters `.sln` project
    entries to `.csproj` only (`if not rel_path.lower().endswith(".csproj"):
    continue`, with a docstring stating this is intentional). Without fixing
    this, `.vbproj` entries in a mixed-language `.sln` are silently dropped
    even after msbuild.py/index.py learn `.vbproj` — VB projects in a real
    mixed C#/VB solution would never be discovered. Must accept both
    extensions.
10. **`ingestion/resolvers/dotnet/index.py`** + **`namespace_map.py`** — include
    `.vb` files; apply `RootNamespace` prepending to every declaration,
    scoped to VB.NET only (see D4 correction above — do not touch C#'s
    namespace resolution).
11. **`ingestion/resolvers/vbnet.py`** + register in
    `resolvers/__init__.py::_RESOLVERS` — resolve `Imports` against the shared
    namespace map, reusing C# resolver logic where identical.

### Phase 5 — Framework edges (ASP.NET + EF Core — D5)
12. **`ingestion/framework_edges/aspnet.py`** — the `language != "csharp"` gate
    is not a single line; it's at least 5 separate gate points across 2 files,
    all needing a `vbnet` variant:
    - `aspnet.py` `_has_aspnet_imports` (file-level language filter)
    - `aspnet.py` `_add_aspnet_edges` (per-file language check)
    - `aspnet.py` `_has_csharp_files` (repo-level gate reused by two handlers)
    - `aspnet.py` `_CSharpExtensionHandler` (host-builder extension-method
      scan, gated via `_has_csharp_files` — easy to miss since it isn't named
      after routes/EF Core)
    - `aspnet_extensions.py` `collect_csharp_texts` (language filter)

    Add VB-syntax detectors (`<ApiController>`, `<HttpGet>`, `Inherits
    DbContext`, `DbSet(Of T)`) alongside lifting each gate. Also extend
    `dynamic_hints/dotnet.py`, which hardcodes a `"*.cs"` glob for class
    discovery — needs a `.vb` variant or VB classes are invisible to dynamic
    hints even after the above gates are lifted.

### Phase 6 — Test fixture & verification
13. **`tests/fixtures/vbnet_solution/`** — a small VB.NET solution
    (`.sln` + `.vbproj`s) mirroring `tests/fixtures/dotnet_solution/`: a couple
    of projects, `Imports`, `Inherits`/`Implements`, a controller +
    `DbContext` for framework-edge coverage.
14. Unit tests for spec, bindings, heritage regex, and resolver;
    run `pytest -k "vbnet or sample_repo"`.

### Docs
15. Update [layers/LANGUAGE_SUPPORT.md](../layers/LANGUAGE_SUPPORT.md) and
    [architecture/language-support.md](language-support.md): VB.NET as a new
    **Good**-tier entry (heritage-via-regex, no health markers yet), with the
    Windows-only-grammar caveat, and a Roadmap row (health markers + AST
    heritage → Full).

---

## 4. Deferred / follow-up

| Item | Trigger to pick up | Reference |
|------|--------------------|-----------|
| Code-health markers (complexity walker map + perf dialect) | Phase 2 PR | D6, `analysis/health/complexity/languages.py` |
| AST-based heritage (drop the regex) | Grammar parses `Inherits`/`Implements` | D3 |
| Cross-platform grammar | Upstream ships Linux/macOS wheels / sdist | D2 |
| Promotion to Full tier | After health markers + AST heritage land | §2 |

**DI-edge resolution (`@param.type` → `type_ref_resolution.py`) — implemented,
not deferred.** Originally scoped out, then added in the same pass after the
user asked to reconsider it. `_resolve_csharp_type_refs` was renamed to
`_resolve_dotnet_type_refs` and registered for both `"csharp"` and `"vbnet"`
in `_STRATEGIES` — it was already fully language-neutral (pure
`DotNetProjectIndex` lookups). What VB actually needed:
- `queries/vbnet.scm`: `@param.type` captures on `constructor_declaration` /
  `method_declaration` / `delegate_declaration` parameter lists. VB's shape
  differs from C#'s — the type lives inside an unfielded `as_clause` child
  (`parameter > as_clause > type:`), not directly on a `type:` field of
  `parameter` itself.
- `parser_helpers.py`: a dedicated `_vbnet_head_type_identifier` (registered
  in `TYPE_HEAD_EXTRACTORS`) — the generic C#-shaped fallback would have
  taken the *first* identifier in a dotted name (`Acme.Domain.IRepo` →
  `"Acme"`) via its generic-subtree-scan branch, not the last/meaningful
  segment (`"IRepo"`) the way C#'s dedicated `qualified_name` handling does.
- `_classify_param_origin` needed **no changes** — VB's
  `constructor_declaration`/`method_declaration`/`delegate_declaration` node
  type strings happen to be identical to C#'s, so the existing
  ancestor-walk table already routes VB captures to the right origin tag.

**Known gap, by design:** a generic `(Of ...)` parameter type parses with
its head still capturable (e.g. `List(Of Widget)` → `"List"`), but the
grammar's ERROR recovery around the `(Of ...)` clause causes whatever
parameter *follows* it in the same signature to drop out of the
parameter list entirely — silently missing from `type_refs`, never
misattributed to a wrong file. Pinned by
`tests/unit/ingestion/test_vbnet_type_use.py::TestGenericParameterGap` so a
future grammar release that fixes it shows up as a visible test change.

---

## 5. Full-suite verification (2026-07-23)

Ran the complete test suite against the pending VB.NET changes before landing:
`uv run pytest -q` → **8279 passed, 15 skipped, 3 failed** (45m09s).

VB.NET-specific tests, isolated (`pytest -k vbnet`): **52 passed, 3 skipped**
(the 3 skips are `testcontainers not installed`, unrelated to VB.NET — they
gate Postgres-backed persistence tests matched incidentally by the `-k`
substring filter).

**The 3 failures are pre-existing and unrelated to this feature.** None of
the failing test files (`test_rewrite_perf.py`, `test_session_model.py`,
`test_job_system.py`) are touched by the VB.NET diff, and none exercise
`ingestion/` code. Confirmed by stashing all pending changes and re-running
just these 3 tests against the clean `main` baseline — **identical failures
reproduced**, so nothing here was introduced or fixed:

- `test_rewrite_perf.py::test_p95_under_100ms` — a hard-coded p95 < 100ms
  latency assertion for `repowise-rewrite`; measured 114–129ms both with and
  without the VB.NET changes. Machine/load-dependent perf flake, not a
  regression.
- `test_session_model.py::test_newest_claude_model_wins_and_normalizes` —
  `FileNotFoundError` writing a fixture `.jsonl` under pytest's tmp path. The
  fixture helper encodes the full absolute repo path into the transcript
  directory name (mirroring real `~/.claude/projects/<encoded-path>/`
  layout), which combined with this machine's long
  `pytest-of-Vasilii.Prodaus\pytest-N\...` tmp root pushes the resulting path
  past Windows' `MAX_PATH`. Pre-existing Windows path-length fragility,
  unrelated to `ingestion/`.
- `test_job_system.py::test_list_jobs_sorted_by_created_at_desc` — two jobs
  created within the same clock tick get ambiguous `created_at` ordering.
  Passed on immediate re-run in isolation; timing-collision flake, not
  VB.NET-related.

No code changes were made in response to these three — they're out of scope
for this feature and pre-date it. No fixes were required for the VB.NET work
itself; the pending diff introduces zero regressions.

---

## 6. Risk register

- **Grammar maturity (v0.1.0):** no heritage parsing, LINQ/preprocessor listed
  as partial, single point-release. Mitigated by regex heritage (D3) and
  per-file degrade-to-passthrough on any parse error. **Also found during
  implementation:** a lexer ambiguity mis-tokenizes a *bare, receiver-less
  call statement* whose target name starts with a VB reserved word used as
  a PascalCase prefix (`DoSomething()`, `TryParse()`, `SubType()` used as a
  standalone statement) — the leading keyword-shaped substring gets split
  off, corrupting that one call edge. Narrow in practice: the same call
  written as an assignment (`x = DoSomething()`), an argument
  (`Foo(DoSomething())`), a member access (`obj.DoSomething()`), or with
  the explicit `Call` keyword all parse correctly — only the "implicit
  call statement with a colliding bare name" shape is affected. Declaring
  a method/class with such a name is unaffected; only the *call site* can
  mis-resolve. Not mitigated — documented as a known grammar limitation.
- **Generic `(Of ...)` types in parameter position:** a second, distinct
  ERROR-recovery gap from the Inherits/Implements one above (D3) — the
  opening `(` of a `(Of T)` clause always lands in its own ERROR node
  wherever it appears (heritage, parameter types, …). In a parameter
  type specifically, this causes whatever parameter *follows* the
  generic-typed one in the same list to drop out of the parsed
  parameter list entirely. Affects only the new DI-edge (`@param.type`)
  feature — silently missing a `type_use` edge for that one trailing
  parameter, never a wrong edge. Pinned by a test (see §4) rather than
  mitigated, matching D3's precedent of accepting the grammar's
  weak spots rather than working around them at the Python layer.
- **Windows-only wheel:** coverage asymmetry across platforms (D2).
- **Framework-edge value:** may never fire on non-ASP.NET-Core VB codebases
  (D5) — cost paid regardless of whether target repos use these frameworks.
- **`build_signature`'s per-node-type (not per-language) dispatch:** VB's
  `method_declaration` node type is the same literal string Go uses, and
  `build_signature` branches on node type alone with no language parameter
  — pre-existing behavior (C# already collides with Go's branch the same
  way), so VB.NET methods get a signature string with a stray `func`
  prefix and a Go-shaped return-type field lookup. Cosmetic only (display
  text, not consumed by resolution or the graph); inherited, not
  introduced by this feature; out of scope to fix here since it would
  require threading a language parameter through a shared, multi-language
  function.
