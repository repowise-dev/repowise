# Language Support: Architecture & Internals

How the language pipeline is built, and how to add a new language. For the
user-facing "what works today" matrix, see
[docs/layers/LANGUAGE_SUPPORT.md](../layers/LANGUAGE_SUPPORT.md).

The pipeline is fully modular. Language identity data lives in a centralised
`LanguageRegistry`; per-language extraction logic lives in `extractors/`;
per-language import resolution lives in `resolvers/`. Adding a language means
dropping one file into each relevant subpackage and registering it in that
subpackage's dispatcher, with **zero changes** to `parser.py`, `graph.py`,
`traverser.py`, or any analysis core file.

---

## How the pipeline processes a file

```
File discovered by FileTraverser
        |
        v
Extension/filename -> LanguageTag  (via LanguageRegistry)
        |
        +-- Config/data language?  -> empty ParsedFile (passthrough)
        +-- Special format?        -> special_handlers.py (OpenAPI/Dockerfile/Makefile/SQL)
        +-- Multi-language file?   -> sfc_source.py projects it to one
        |                             grammar's language at identical offsets
        +-- Has grammar?           -> tree-sitter AST parsing
                |
                v
        .scm query extracts:
          @symbol.def / @symbol.name         -> Symbol nodes
          @import.statement / @import.module -> Import edges
          @call.target / @call.receiver      -> Call edges
                |
                v
        Per-language extractors:
          - Named bindings (import name -> source symbol)
          - Heritage (extends/implements/traits)
          - Docstrings (Python, JSDoc, GoDoc, Rustdoc, Javadoc)
          - Visibility (public/private/protected)
                |
                v
        GraphBuilder resolves imports:
          Python: dotted module paths via a source-root-aware module index
                  (src/ + monorepo packages/*/src + PEP 420 namespace
                  packages), __init__.py re-export barrels, stem fallback
          TS/JS:  relative paths, tsconfig aliases, workspace exports,
                  `export ... from` re-export barrels, node_modules,
                  package.json "imports" (`#alias/*`) subpath imports
          Svelte: the TS/JS resolver plus SvelteKit's `$lib` -> src/lib
          Go:     go.mod module path stripping
          Rust:   crate::/self::/super::, mod.rs probing
          C/C++:  compile_commands.json include directories
          Dart:   package:/dart:/relative URIs via the pubspec name map +
                  library-name index (dotted part-of)
          Lightweight tier (Elixir/Clojure/Haskell/Lean 4/Erlang/F#):
                  regex-extracted imports vs a declared-module-name index
          dbt:    ref()/source() vs a per-project model-name index
          Other:  stem-map fallback (filename matching)
                |
                v
        Graph analysis:
          PageRank, community detection, dead code, execution flows
```

---

## Module layout

Per-language code lives in dedicated subpackages so adding a language means
dropping a file into each rather than editing monoliths.

```
ingestion/
  languages/           # LanguageRegistry + LanguageSpec (identity data)
    spec.py            #   LanguageSpec dataclass (the schema)
    registry.py        #   LanguageRegistry lookup interface + REGISTRY singleton
    specs/             #   one module per language, each exporting `SPEC`
      __init__.py      #     aggregates every SPEC into ordered `ALL_SPECS`
      python.py  typescript.py  go.py  rust.py  csharp.py  …  (44 tags)
    python_modules.py  #   dotted-module <-> file index (src / monorepo / PEP 420)
  extractors/          # Per-language AST extraction
    visibility.py      #   symbol visibility (public/private/protected)
    signatures.py      #   human-readable signature building
    docstrings.py      #   module + symbol docstring extraction
    bindings/          #   import name + alias binding extraction (per-lang)
      __init__.py      #     extract_import_bindings dispatcher
      python.py  ts_js.py  go.py  rust.py  java.py  kotlin.py
      ruby.py    csharp.py swift.py scala.py php.py cpp.py dart.py
    heritage/          #   inheritance/interface/trait extraction (per-lang)
      __init__.py      #     extract_heritage + HERITAGE_EXTRACTORS dispatcher
      python.py  ts_js.py  java.py  go.py    rust.py  cpp.py
      kotlin.py  ruby.py   swift.py csharp.py scala.py php.py dart.py
  resolvers/           # Per-language import resolution
    python.py          #   dotted imports via module index: __init__.py
                       #   barrels, src/ + monorepo packages/*/src, namespace pkgs
    typescript.py      #   multi-ext probe, tsconfig aliases
    go.py              #   go.mod module path stripping
    rust.py            #   crate::/self::/super::, mod.rs probing
    cpp.py             #   compile_commands.json include paths
    kotlin.py          #   package-to-directory mapping (shared JVM index)
    ruby.py            #   require/require_relative resolution
    csharp.py / dotnet/ #  namespace-based + MSBuild project graph
    swift.py           #   module import resolution
    scala.py           #   package-to-directory mapping (shared JVM index)
    php.py             #   namespace/PSR-4 resolution
    sql.py             #   dbt ref()/source() lineage
    generic.py         #   stem-matching fallback
  framework_edges/     # Framework convention edges (one module per framework + base.py)
                       #   __init__.py re-exports add_framework_edges; iterates FrameworkHandler list
                       #   django/fastapi/flask/aspnet/rails/laravel/spring/express/go/rust/typo3
                       #   hono/next_app/quarkus/micronaut/trpc/remix/gtest + pytest_edges
  dynamic_hints/       # Per-language dynamic-edge extractors
    base.py            #   DynamicHintExtractor + DynamicEdge
    registry.py        #   HintRegistry
    django.py  pytest_hints.py  python_imports.py  node.py  dotnet.py
    spring.py  ruby.py  php.py  scala.py  swift.py  c.py  cpp.py  luau.py  go.py  jvm.py
  sfc_source.py        # Multi-language-file (SFC) projection (see below)
  parser.py            # ASTParser (language-agnostic orchestration)
  graph.py             # GraphBuilder (import/call/heritage resolution)

analysis/
  dead_code/           # Dead code detection
    analyzer.py        #   DeadCodeAnalyzer class + detection passes
    models.py          #   DeadCodeKind, DeadCodeFindingData, DeadCodeReport
    constants.py       #   never-flag globs, framework decorators, fixtures
    dynamic_markers.py #   per-language source-text dynamic markers
```

The source-of-truth registry is
`ingestion/languages/specs/__init__.py`, which builds `ALL_SPECS`
(order-significant, first-spec-wins extension map) from one `specs/<tag>.py`
module per language. `LanguageSpec`'s `import_support` field
(`"full" | "partial" | "none"`) is the closest formal signal to a "tier";
the tier names in the user-facing doc are a documentation grouping layered on
top of `import_support` plus the presence of binding / heritage / resolver
modules. (The section-header comments inside `specs/__init__.py` predate the
current state and should not be treated as a live tier reference.)

---

## Adding a new language

### Step 1: Add a `LanguageSpec` module

Language identity data lives in `languages/specs/`, **one module per
language**. Create
`packages/core/src/repowise/core/ingestion/languages/specs/mylang.py`
exporting a single `SPEC`:

```python
"""LanguageSpec for mylang."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="mylang",
    display_name="MyLang",
    extensions=frozenset({".ml"}),
    grammar_package="tree_sitter_mylang",       # PyPI package name
    scm_file="mylang.scm",                       # query file name
    heritage_node_types=frozenset({"class_declaration"}),
    entry_point_patterns=("main.ml",),
    manifest_files=("mylang.toml", "mylang.build.json"),
    build_config_manifests=("mylang.build.json",),  # usually empty — see below
    shebang_tokens=("mylang",),
    builtin_calls=frozenset({"print", "len"}),  # filter from call graph
    builtin_parents=frozenset({"Object"}),       # filter from heritage
    color_hex="#AB47BC",
)
```

#### `manifest_files` also decides package boundaries

Every name in `manifest_files` that is *not* in `build_config_manifests` marks
its directory as a **package root**. That drives two things beyond ingestion:
monorepo detection (`RepoStructure.packages`), and code health's `module`
attribution — the label the dashboard, the module rollups and `module:` target
expansion all group on. Declaring your manifests is therefore all it takes to
give a monorepo in your language proper per-package health; there is no second
list to edit. `REGISTRY.package_manifest_filenames()` is the single source of
truth, and both consumers read it.

`build_config_manifests` defaults to empty, which is nearly always right. Add
to it only when a name in `manifest_files` configures a build rather than
declaring a distributable unit, because such files appear in directories that
are not packages and each one becomes a false root that fragments the rollup.
The shipped cases are the .NET `Directory.Build.props` / `global.json` family
(measured in 135 non-package directories), `vite.config.js` / `nuxt.config.ts`,
`svelte.config.js`, Cabal's `Setup.hs`, and `lean-toolchain`.

`manifest_files` holds exact filenames only, so a language whose package file is
a *pattern* cannot be expressed. .NET is the live example: `*.csproj` is the
real package declaration, everything C# declares is build configuration, and a
.NET monorepo therefore still falls back to the top-level directory.

One caveat worth knowing: package roots are read from a **directory scan**, not
from the indexed file list, because the traverser only emits files whose
language it can detect and drops 18 manifest names on that rule (`go.mod`,
`pom.xml`, `build.gradle`, `Gemfile`, `build.sbt` among them). So a manifest
counts as a package root whether or not your language's files parse.

Then register it in `languages/specs/__init__.py` by importing the module and
slotting it into the `ALL_SPECS` tuple. **Order matters**: `LanguageRegistry`
builds its extension map first-spec-wins, so place more specific languages
ahead of ones that share an extension (e.g. TypeScript before JavaScript). A
spec using `shares_grammar_with` must also come *after* the spec it borrows
from, which is resolved against the registry built so far. You never edit
`registry.py` itself.

### Step 2: Add the `LanguageTag`

Add `"mylang"` to the `LanguageTag` Literal type in
`packages/core/src/repowise/core/ingestion/models.py`. (`EXTENSION_TO_LANGUAGE`
and `SPECIAL_FILENAMES` are derived from the registry filtered to these tags,
so this remains a required manual step.)

### Step 3: Write a tree-sitter query file

Create `packages/core/src/repowise/core/ingestion/queries/mylang.scm` using
tree-sitter S-expression syntax. Follow the capture-name conventions:

| Capture | Purpose | Required? |
|---------|---------|-----------|
| `@symbol.def` | Full definition node (line numbers, kind lookup) | Yes |
| `@symbol.name` | Name identifier | Yes |
| `@symbol.params` | Parameter list | No |
| `@symbol.modifiers` | Decorators / visibility modifiers | No |
| `@symbol.receiver` | Go-style method receiver | No |
| `@import.statement` | Full import node | Yes |
| `@import.module` | Module path being imported | Yes |
| `@call.target` | Function/method being called | No (enables call graph) |
| `@call.receiver` | Object the call is made on | No |
| `@call.arguments` | Call arguments | No |

`python.scm` and `typescript.scm` are good starting points. A language whose
syntax *is* another's can skip this step entirely by pointing `scm_file` at the
existing query file — that field names the query to load, so `svelte` declares
`scm_file="typescript.scm"` and writes no `.scm` of its own.

### Step 4: Add a `LanguageConfig` entry

Add a parser configuration to `LANGUAGE_CONFIGS` in
`packages/core/src/repowise/core/ingestion/language_configs.py` (re-exported
from `parser.py` for back-compat):

```python
"mylang": LanguageConfig(
    symbol_node_types={
        "function_definition": "function",
        "class_definition": "class",
    },
    import_node_types=["import_statement"],
    export_node_types=[],
    visibility_fn=public_by_default,  # from extractors.visibility
    parent_extraction="nesting",
    parent_class_types=frozenset({"class_definition"}),
    entry_point_patterns=["main.ml"],
),
```

### Step 5: Add the tree-sitter grammar dependency

Add the grammar package to `pyproject.toml`:

```toml
[project]
dependencies = [
    # ...
    "tree-sitter-mylang>=0.23,<1",
]
```

### Step 6 (optional): Binding extractor

For full-tier support, add an extractor module under
`extractors/bindings/mylang.py` and register it in the
`extract_import_bindings()` dispatcher. Without this, imports are still
resolved but named-binding-level call resolution won't work.

### Step 7 (optional): Heritage extractor

Add a module under `extractors/heritage/mylang.py` and register it in the
`HERITAGE_EXTRACTORS` dict. Without this, inheritance chains won't appear in
the graph.

### Step 8 (optional): Import resolver

If the language has a non-trivial import system, create a resolver in
`resolvers/mylang.py` and register it in the `_RESOLVERS` dict in
`resolvers/__init__.py`. For simple languages, the generic stem-map fallback
(matching by filename) works out of the box.

### Verify

```bash
# Run the parser tests
pytest tests/ -k "mylang or sample_repo" -x

# Index a real project
repowise init /path/to/mylang-project
```

No changes are needed to `traverser.py`, `dead_code.py`, `page_generator.py`,
`cost_estimator.py`, or any other consumer file, they all derive their
language sets from the registry automatically.

---

## Multi-language files (the SFC pattern)

Some file types hold more than one language. A `.svelte` or `.vue` component is
TS/JS in its `<script>` blocks, framework-flavoured HTML in its markup, and CSS
in `<style>`. The markup grammars parse the file but return each `<script>`
body as one opaque `raw_text` node, so a `.scm` query against them captures no
symbol, import, or call — a grammar alone cannot support such a language.

`ingestion/sfc_source.py` solves this with a **byte-preserving projection**
rather than a second coordinate space:

1. a markup grammar *locates* the JS-bearing regions — every `<script>` body
   and every markup expression;
2. every byte outside those regions is blanked to a space, with newlines kept;
3. each kept markup expression is fenced by rewriting its two surrounding
   delimiter bytes to `;`, so adjacent expressions cannot run together and an
   unterminated final script statement cannot swallow the following expression
   via ASI.

The result is valid TypeScript whose every byte offset and line number matches
the original file. That single property is what makes the rest free: the spec
declares `shares_grammar_with="typescript"` and `scm_file="typescript.scm"`, the
`LanguageConfig` is an alias of TypeScript's, and the three health dialect
registries alias the TS entries. Each consumer that hands raw bytes to a
tree-sitter `Parser` calls `prepare_source(language, source, path=abs_path)`
first — the ingestion parser plus the complexity, dataflow, and duplication
walkers. It is a no-op for every language without a registered locator or
sanitizer.

`prepare_source` also carries per-language byte-preserving sanitizers that
aren't about multi-language files at all — a construct a grammar can't parse,
where hitting it corrupts everything downstream. Object Pascal is the current
example: `.dpr`/`.dpk`/`.lpr` project files write `unit in 'path.pas'` clauses
in their `uses` list, a syntax tree-sitter-pascal has no rule for, and hitting
one used to corrupt every unit named after it in the same clause. Its
sanitizer (`prepare_pascal_source` in `ingestion/parser_helpers.py`) is gated
on `path`'s extension — that syntax is invalid in a plain `.pas`/`.pp` unit
file — and is a no-op everywhere else, same contract as the `_LOCATORS` path.
Registering a sanitizer this way, rather than as an if-block in `parser.py`,
means it stays "zero changes to `parser.py`" for a new language and the health
walkers get the same clean projection the ingestion parser does.

### The locator registry

Only step 1 differs per language, so it lives behind `_LOCATORS`, a dict of
`Locator(grammar_module, visit, component_name)`. The blanking, fencing,
caching and offset invariants are shared; adding a markup language means adding
a `Locator`, not a second copy of the walker.

| | Svelte | Vue |
|---|---|---|
| Grammar | `tree-sitter-svelte` | `tree-sitter-html` |
| Expression nodes | `svelte_raw_text` under `expression` / `if_start` / `key_start` / `html_tag` | `attribute_value` inside `quoted_attribute_value`; `{{ … }}` scanned inside `text` |
| Fence bytes | the surrounding `{` `}` | the surrounding `"` or `'` |
| Skipped binding forms | `{#each}`, `{#await}` heads | `v-for`, `v-slot` / `#default` |
| Non-component tags | the `svelte:*` namespace | `<KeepAlive>`, `<Transition>`, `<RouterView>`, … in either spelling |

There is no `tree-sitter-vue` on PyPI. The HTML grammar parses a Vue SFC
cleanly anyway, because `<template>`, `<script>` and `<style>` are ordinary
elements to it — so one dependency covers both Vue and plain HTML.

**Plain HTML deliberately has no locator.** It reuses the same grammar but not
the projection, and the distinction is the point: a projection exists to turn a
`<script>` block into analysable TypeScript, which is worth it when that block
is where the component lives. A plain `.html` file's inline script almost never
carries a module import — 13 of the 6162 `.html` files in the validation corpus
(0.2%) — so projecting would buy a rounding error and would mint symbols,
contradicting HTML's import-only tier. Its `<script src>` / `<link href>`
attributes are read directly in
`lightweight_imports/html.py`, which is extractor work, not projection work.
Adding a `Locator` for HTML purely for symmetry with Vue and Svelte would be a
mistake; `_LOCATORS` is for languages whose *script blocks* need projecting.

Vue's expressions live in attribute *values*, which is why the fence bytes are
quotes rather than braces, and why only directive attributes (`:`, `@`, `v-`)
are projected: `class="btn primary"` is a literal string, and projecting it
would put two juxtaposed identifiers at statement position.

Two things markup carries that the projection cannot express are minted
separately: the component symbol itself (via
`extractors/synthetic_symbols/sfc_component.py`, since the filename is the only
thing that names a component) and `<Foo />` instantiation call edges (via
`component_call_sites`, the analogue of `tsx.scm`'s JSX captures). For Vue both
run the filename and the tag through the *same* normaliser, so
`back-to-top.vue` and `<back-to-top />` cannot disagree about the name
`BackToTop`.

The same steps would fit Astro components; only the locator changes.

---

## Optional language-specific passes

Several pluggable hooks let a language opt into deeper resolution without
touching the shared pipeline files:

- **`graph_warmups.py`**, register a one-time pre-import warmup (e.g. building
  a project index) so its cost shows up as its own phase instead of inflating
  `graph.imports`. Warmups can also set `is_never_flag=True` on file nodes a
  build manifest declares secondary (the dead-code analyzer consults this in
  `_should_never_flag`), so each repo's own build files teach the analyzer what
  to ignore without extending the hardcoded glob list. Used today by
  `_warmup_jvm` to exempt Gradle non-`main` source sets automatically.
- **`type_ref_resolution._STRATEGIES`**, register a strategy that resolves
  parameter-type captures (`@param.type`) to file-level `type_use` edges.
  Drives DI-aware analysis and keeps type-only interfaces off the dead list.
- **`languages/<lang>_member_reads.py`**, emit `reads` edges for property /
  member access. Used today for C# `var x = new T()` locals; the same shape
  applies to any statically-typed language.
- **`extractors/synthetic_symbols.py`**, recognise source-generator attributes
  and emit the symbols the generator would produce at compile time. Used today
  for CommunityToolkit MVVM (`[ObservableProperty]`, `[RelayCommand]`) and JVM
  Lombok / `record` / Kotlin `data class`; the same shape fits Kotlin
  `@Parcelize`, etc.
- **`extractors/visibility.py::refine_<lang>_visibility`**, node-aware
  visibility refinement for languages where access is dictated by AST context
  (C/C++ access specifiers, storage class, export attributes) rather than
  modifier text alone.

The three code-health signal layers are each driven by the same
per-language plugin pattern, registered in a dict exactly like `resolvers/`:

- **`analysis/health/complexity/languages.py`** (`LANGUAGE_MAPS`), the
  code-health complexity walker's per-language node-type map, independent of the
  ingestion `.scm` queries. A map gives McCabe complexity, nesting, cognitive
  complexity, and per-function markers; optional `class_kinds` /
  `self_identifiers` / `member_access_kinds` add class-level metrics (LCOM4,
  god-class), and `assert_kinds` / `assert_call_kinds` add assertion-block
  smells. Ships for the 11 Full languages plus Dart. See `complexity/README.md`.
- **`analysis/health/perf/dialects/`** (`PERF_DIALECTS`), the **performance**
  signal. A `PerfDialect` owns callee extraction (the per-grammar seam), the
  execution-sink lexicon (`sink_kind`), the constant-loop / string-concat /
  async predicates, and its own marker list, so Go contributes `defer_in_loop`,
  Java/Go contribute `regex_compile_in_loop`, C# contributes
  `blocking_sync_in_async`, and the Phase-7a loop markers are opt-in per
  dialect. Every method has a safe "no signal" default.

  Three hooks answer questions that recur in every language, so a new dialect
  reuses them instead of re-deriving them:
  - `block_loop_body(node)` — the per-iteration body when the language's real
    iteration idiom is a call taking a block/lambda (Ruby `items.each do … end`,
    Kotlin `ids.forEach { … }`). The walker then applies every loop rule (body
    scoping, constant-bound skip, nesting, the same-collection quadratic gate)
    to it exactly as to a native loop. A lambda returned this way is *not*
    treated as a deferred scope, so `loop_depth` survives the boundary.
  - `loop_body(node)` — the per-iteration body of a *native* loop, defaulting to
    the `body` field. Only tree-sitter-kotlin leaves that field unlabeled; the
    override is what keeps a sink in a `for (u in repo.findAll())` **header**
    from reading as a sink inside the loop.
  - `resets_per_iteration(node, name, loop_kinds)` + `binds_name(node, name)` —
    the shared answer to the top `string_concat_in_loop` false positive, an
    accumulator declared fresh each pass (`var s = ""; s += part`) which is
    bounded per iteration rather than an O(n²) rebuild. The traversal is shared;
    only the "does this statement bind the name" question is per-grammar. (The
    Python, Ruby and Dart dialects predate the hook and keep their own tuned
    versions.)
- **`analysis/health/dataflow/dialects/`** (`DEFUSE_DIALECTS`), the
  **dataflow** layer (intra-procedural CFG + def/use + reaching definitions,
  powering **Extract Method**). A `DefUseDialect` owns the read-vs-write
  classification of each statement and the parameter binders; the CFG builder,
  the reaching-definitions fixpoint, and the Extract Method slicer stay
  language-agnostic (the control-flow grammar they branch on lives on the
  `LanguageNodeMap`). The full pass runs only for functions a structural marker
  already flagged (`large_method` / `brain_method` / `complex_method`), so it
  stays within the health-pass budget.

  A dialect is only half the requirement: the CFG builder resolves bodies,
  conditions and jumps through *field names* (`body` / `consequence` /
  `alternative`) and *node types* (`break_kinds` / `continue_kinds`), so a
  grammar that labels none of them cannot be served by a dialect alone. The
  slicer additionally refuses any span containing a jump — which means an
  untyped jump is worse than no signal, because it would license an extraction
  that silently changes control flow. `find_extractions` also refuses a function
  whose subtree carries a parse error (`Node.has_error`): macro-heavy C/C++
  headers make tree-sitter emit one bogus `function_definition` spanning a whole
  class, and proposing to lift "statements" out of that is a wrong suggestion.

All tiers are purely additive and degrade to silence: an unmapped language
produces no findings rather than wrong ones.

---

## Workspace contract extraction

In workspace mode (multiple repos indexed together), repowise links
service-to-service API contracts (HTTP routes, gRPC services, and DB tables) so
a provider endpoint in one repo connects to its consumers in another. The
extractors live in `core/workspace/extractors/` and follow the same
dialect-plugin shape: the orchestrator owns only the file walk, and each
framework / client library is an independent module registered in a tuple.

```
workspace/extractors/
  base.py            # iter_source_files walk + ScanContext (shared by all)
  langs.py           # registry-derived extension sets (JS_TS, PYTHON, RUST, …)
  http/
    dialect.py       #   HttpDialect protocol + build_provider/consumer_contract
    paths.py         #   normalize_http_path + URL helpers
    express.py  fastapi.py  spring.py  laravel.py  go.py  aspnet.py  # providers
    js_clients.py  python_clients.py  csharp_http.py  rust_clients.py # consumers
    rust_axum.py  mounts.py                                          # providers
    __init__.py      #   HttpExtractor + PROVIDER_DIALECTS / CONSUMER_DIALECTS
  grpc/
    dialect.py       #   GrpcDialect protocol + make_grpc_contract
    proto.py  go.py  java.py  python.py  typescript.py  csharp.py
    __init__.py      #   GrpcExtractor + DIALECTS
  data/              #   table providers (DDL / ORM entities) <-> SQL consumers
```

A dialect declares the file extensions it understands (via `langs.py`) and
turns regex matches into `Contract`s through shared builders, so every dialect
emits identically-shaped providers/consumers and path-normalization lives in
one place. **Adding a framework or client** means dropping one module into
`http/`, `grpc/`, or `data/` and appending its dialect to the relevant registry
tuple, no orchestrator edits.

| Contract | Providers | Consumers |
|----------|-----------|-----------|
| **HTTP** | Express, FastAPI, Spring, Laravel, Go (gin/echo/chi/net-http), ASP.NET (attribute + minimal), Rust (Axum routes, Actix/Rocket attribute macros) | `fetch` / `axios` / URL-literal wrappers (JS/TS), `requests` / `httpx` (Python), `HttpClient` / `UnityWebRequest` / Best.HTTP (C#), `reqwest` (Rust) |
| **gRPC** | `.proto` IDL, Go, Java, Python, NestJS (`@GrpcMethod`), C# (gRPC-dotnet) | Go, Java, Python, C# |
| **Data** | DDL `CREATE`/`ALTER`, Alembic `op.create_table`, ORM entities (SQLAlchemy, SQLModel, Django, JPA, EF Core, ActiveRecord, Eloquent) | SQL string literals in app code (sqlglot-parsed, verb-anchored-regex fallback) |

See [docs/scale/WORKSPACES.md](../scale/WORKSPACES.md) for the user-facing workspace guide.

---

## See also

- [docs/layers/LANGUAGE_SUPPORT.md](../layers/LANGUAGE_SUPPORT.md), user-facing support matrix
- [docs/layers/CODE_HEALTH.md](../layers/CODE_HEALTH.md), code-health markers and per-language precision hazards
- [architecture/code-health.md](code-health.md), code-health layer internals
- [architecture/ARCHITECTURE.md](ARCHITECTURE.md), full system architecture
