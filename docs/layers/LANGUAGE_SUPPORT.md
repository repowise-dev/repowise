# Language Support

repowise parses **17 languages to a full AST**, resolves imports and call
graphs across them, and scores **12 at the Full tier** with code-health markers.
Everything else in your repo is still tracked through git history and appears in
the wiki. This page is the "what works for my language today" reference.

> **How to add a language, and how the pipeline works internally:** see
> [architecture/language-support.md](../architecture/language-support.md). Adding
> a language needs one `.scm` query file and one config entry, with no changes
> to the parser core.

---

## Tiers at a glance

Every language falls into one tier. The tier determines which pipeline stages
produce meaningful output.

| Tier | Languages | What works |
|------|-----------|------------|
| **Full** | Python · TypeScript · JavaScript · Svelte · Java · Kotlin · Go · Rust · C++ · C# · Scala · Ruby | AST parsing, import resolution, named bindings, call resolution, heritage, docstrings, framework-aware edges, dynamic-hint extractors, and **code-health markers** |
| **Good** | C · Swift · PHP · Dart | Everything above except code-health markers (C, Swift, PHP; Dart *does* get health markers). Dedicated workspace resolvers and framework edges per language |
| **SQL / dbt** | `.sql` via sqlglot | Tables / views / functions / procedures as symbols with wiki pages; dbt projects get real `ref()` / `source()` lineage |
| **Shell** | `.sh` `.bash` `.zsh` | Function definitions as symbols, `source` / `.` import edges (incl. `$SCRIPT_DIR` / `dirname` / `$BATS_ROOT` idioms), and function-level code-health complexity (CCN, nesting, cognitive). No class metrics, heritage, bindings, or dead-code flagging |
| **Config / data** | OpenAPI · Protobuf · GraphQL · Dockerfile · Makefile · YAML · JSON · TOML · Terraform · Markdown | In the file tree and wiki; special handlers extract endpoints / targets where applicable |
| **Lightweight** | Elixir · Clojure · Haskell · Lean 4 · Erlang · F# | Regex-tier file-level import graph (no symbols/calls). Honest file-to-file dependencies, no symbol-level claims |
| **Partial** | Luau / Roblox | AST symbols + `require()` resolution (Rojo / `.luaurc` aware); no health markers yet |
| **Structural** | Objective-C · R · Zig · Julia · Elm · OCaml · Crystal · Nim · D | Git history only (blame, hotspots, co-change). No AST parsing |

**Pipeline stage coverage:**

| Stage | Full | Good | Lightweight | Structural | Config / Data |
|-------|:----:|:----:|:-----------:|:----------:|:-------------:|
| File discovery & git history | ✅ | ✅ | ✅ | ✅ | ✅ |
| AST symbol extraction | ✅ | ✅ | - | - | - |
| Import resolution | ✅¹ | ✅ | regex | - | - |
| Call graph edges | ✅ | ✅ | - | - | - |
| Heritage (extends/implements) | ✅ | ✅ | - | - | - |
| Named bindings | ✅ | ✅ | - | - | - |
| Code-health markers | ✅² | Dart only | - | - | - |
| Dead code detection | ✅ | ✅ | ✅ | ✅ | - |
| Semantic search & wiki pages | ✅ | ✅ | ✅ | ✅ | ✅ |

¹ Scala's import resolution is partial (shared JVM index with SBT/Mill
build-file fallback); every other Full and Good language resolves imports
fully.
² See [code-health coverage](#code-health-coverage), a language is only "Full"
once it clears the health checklist.

---

## Full tier

Complete pipeline coverage: AST parsing, import resolution, call resolution,
named bindings, heritage, docstrings, framework-aware edges, dynamic-hint
extractors, and code-health markers.

| Language | Extensions | Import style |
|----------|-----------|--------------|
| **Python** | `.py` `.pyi` | `import x` / `from x import y`; source-root-aware module index (src/, monorepo `packages/*/src`, PEP 420), `__init__.py` re-export barrels |
| **TypeScript** | `.ts` `.tsx` | ESM / `require()` with tsconfig path aliases, npm/yarn/pnpm workspaces, `export * from` barrels, optional `.vue`/`.svelte`/`.astro` probing |
| **JavaScript** | `.js` `.jsx` `.mjs` `.cjs` | `import` / `require()` including CommonJS re-export shapes and member picks |
| **Svelte** | `.svelte` | Same resolver as TS/JS, plus SvelteKit's `$lib` alias and Node `#`-prefixed subpath imports; `$app/*` / `$env/*` stay external (virtual modules) |
| **Java** | `.java` | `import pkg.Class` / `.*` / `import static` with Maven + Gradle reactor discovery, JPMS recognition, package fan-out |
| **Kotlin** | `.kt` `.kts` | Shares the JVM workspace index with Java (cross-language resolution); `.kt` under `src/main/java` recognised |
| **Go** | `.go` | `import "path"` with multi-module `go.mod` discovery; a package import fans out to every file in the package |
| **Rust** | `.rs` | `use crate::` / `super::` / `self::` with `Cargo.toml` |
| **C++** | `.cpp` `.cc` `.cxx` `.h` `.hpp` `.hxx` | `#include` via `compile_commands.json` + CMake / Bazel workspace header maps, header↔implementation pairing |
| **C#** | `.cs` | `using` / `global using` / `using static` / aliases with `.csproj` / `.sln` resolution; MSBuild project graph; `partial` class linking |
| **Scala** | `.scala` | `import pkg.Foo`, brace/wildcard/package imports via the shared JVM index (cross-language with Java/Kotlin); SBT / Mill build parsing as fallback (partial import resolution¹) |
| **Ruby** | `.rb` | `require` / `require_relative` with `$LOAD_PATH` probing, Gemfile externals, RSpec mirror edges, Rails / Zeitwerk autoloading |

All twelve also support three-tier call resolution (same-file, cross-file,
global stem match) and docstring extraction (Python, Ruby comments, JSDoc,
GoDoc, Rustdoc, Javadoc, Scaladoc, Doxygen, XML doc).

**Svelte components** are three languages in one file, so they get a dedicated
projection rather than a grammar of their own. `tree-sitter-svelte` locates the
`<script>` blocks and the markup `{expressions}`; everything else (markup,
`<style>`) is blanked to spaces with newlines preserved, and the result is
parsed as TypeScript at **byte-identical offsets**. So a component reuses the
TypeScript queries, config, and all three health dialects verbatim, and every
line number points at the real `.svelte` file. Three Svelte-specific pieces sit
on top:

- the **component itself** becomes a class-kind symbol named after the file
  (`Button.svelte` → `Button`), since nothing in the source names it;
- **`<Foo />` in markup** mints a call edge on `Foo`, the same way `tsx.scm`
  treats a JSX element;
- **markup expressions are kept**, so a handler referenced only from
  `on:click={inc}` still carries an edge instead of reading as dead code.

Two deliberate ceilings: `{#each x as y}` / `{#await …}` heads are Svelte block
syntax rather than JS expressions and are skipped, as are object-literal
attributes (`use:action={{ a, b }}`), which read as a block at statement
position. And a component's `export let` props are set by the parent as markup
attributes, never imported by name, so the unused-export pass is suppressed for
`.svelte` — the alternative would flag every prop in the repo.

**Framework-aware edges** connect routes to handlers, DI registrations to
implementations, and ORM entities to relationships:

| Language | Frameworks |
|----------|-----------|
| Python | Django, FastAPI, Flask, pytest fixtures |
| Ruby | Rails (routes → controller actions, Zeitwerk autoloading), RSpec mirror edges |
| Java / Kotlin | Spring (stereotypes, `@RequestMapping`, Spring Data, `@Bean`), Jakarta / JPA, Quarkus, Micronaut, Android manifest |
| C# | ASP.NET (attribute + minimal API), EF Core, gRPC-dotnet, host-builder extension methods, CommunityToolkit MVVM |
| Go | net/http, gin, echo, chi, gRPC server registration |
| Rust | Axum, Actix route → handler |
| JS / TS / Svelte | Next.js App Router, Hono / Fastify / Koa / Elysia, Remix / SvelteKit (`+page.svelte`, `+layout.svelte` and their `.ts` siblings) / Astro, tRPC, Express / NestJS |
| C++ | GoogleTest, Catch2, Boost.Test, doctest, Google Benchmark, libFuzzer |

The dead-code analyzer understands each ecosystem's entry points, generated-file
conventions, and never-flag globs so build products and framework-invoked code
aren't reported as unreachable. (Full per-language detail:
[architecture/language-support.md](../architecture/language-support.md).)

---

## Good tier

AST parsing, symbol extraction, import resolution, call resolution, named
bindings, and heritage (Swift extension conformance, PHP trait use, Dart
mixins). Dedicated workspace resolvers per language.

| Language | Extensions | Import style |
|----------|-----------|--------------|
| **C** | `.c` | `#include` via `compile_commands.json` (shares C++ grammar) |
| **Swift** | `.swift` | `import` with SPM `Package.swift` target → directory mapping; intra-module type references; `@main` entry points |
| **PHP** | `.php` | `use Foo\Bar\Baz` with composer.json PSR-4 longest-prefix resolution; Laravel, TYPO3 edges |
| **Dart** | `.dart` | `import` / `export` / `part` URIs; `package:` via every `pubspec.yaml`; Flutter route tables and `runApp()` edges; **code-health markers** |

---

## SQL + dbt

SQL is parsed by a dedicated sqlglot handler (multi-dialect, error-tolerant)
rather than tree-sitter, plus the lightweight import tier for dbt lineage.

- **DDL symbols** (any `.sql` file), `CREATE TABLE` / `VIEW` /
  `MATERIALIZED VIEW` become class-kind symbols with columns in the signature;
  `CREATE FUNCTION` / `PROCEDURE` become function-kind symbols, with wiki pages
  and `get_symbol` lookups. Set `sql_dialect` in config for dialect-specific
  syntax (`postgres`, `mysql`, `tsql`, `clickhouse`, …). Any parse problem
  degrades the file to passthrough, never a crash, never a guess.
- **dbt lineage** (gated on `dbt_project.yml`), `{{ ref('model') }}` and
  `{{ source('schema', 'table') }}` become real import edges resolved against a
  per-project model-name index, so model-level lineage, hotspots, co-change,
  ownership, and communities all fall out free.
- **App-to-database contracts** (workspace mode), pairs table *providers* (DDL,
  Alembic, ORM entities) with table *consumers* (SQL string literals in app
  code) into `data` contracts on the Live System Map. See
  [WORKSPACES.md](../scale/WORKSPACES.md).
- **Health markers**, stored routines get cyclomatic complexity;
  `sql_select_star`, `sql_update_delete_without_where`, and `sql_cartesian_join`
  ride the sqlglot AST. All uncalibrated by construction and never move the
  defect headline. See [CODE_HEALTH.md](CODE_HEALTH.md).

---

## Lightweight, Partial, and Structural tiers

**Lightweight** (Elixir, Clojure, Haskell, Lean 4, Erlang, F#), no AST parsing,
but a real file-level import graph from a regex tier: import statements are
extracted per-language and resolved against a declared module-name index. The
knowledge graph runs in flow/sparse mode on the result: honest file-to-file
dependencies, no symbol-level claims. F# additionally honours the fsproj
`<Compile Include>` compile-order spine.

**Partial** (Luau / Roblox), AST symbols, Luau type aliases, and `require(...)`
capture are wired. Import resolution handles string literals, `script` relative
instance paths (including `:WaitForChild` idioms), absolute Roblox paths via
Rojo's `default.project.json`, and `@alias` requires via `.luaurc`. No health
markers yet.

**Shell** (`.sh` / `.bash` / `.zsh`), function definitions (both `foo()` and
`function foo` forms) become symbols, `source` / `.` statements become import
edges, and calls to functions defined in the same or a sourced file resolve to
call edges (external binaries like `grep` mint no edge). Import resolution
covers literal relative paths plus the common directory-anchor idioms
(`$SCRIPT_DIR/x.sh`, `$(dirname "$0")/x.sh`, `${BASH_SOURCE%/*}/x.sh`) and
project-root anchors (`$BATS_ROOT/$LIBDIR/lib/x.sh`) via a unique path-suffix
match; genuinely dynamic paths (`source "$1"`) stay external. Shell also gets
**function-level complexity** markers (CCN / nesting / cognitive, with `&&` /
`||` command lists counted). tree-sitter-bash parses the bash/POSIX subset, so
zsh mostly works and fish does not; any parse error degrades that file to
passthrough. No class metrics, heritage, bindings, or dead-code flagging (shell
scripts are invoked by name, so static reachability is meaningless).

**Structural** (Objective-C, R, Zig, Julia, Elm, OCaml, Crystal, Nim, D) -
tracked in git history (blame, hotspots, co-change) but no AST parsing. Files
appear in the wiki as traversal-level entries, and the knowledge graph runs in
structural mode: it orients by directory structure, naming, and git evidence,
and never claims an execution flow it cannot see.

---

## Code-health coverage

Code-health markers run off a per-language complexity-walker map that is
**independent** of `.scm` parsing, a language can parse perfectly for the graph
yet still need this map before health markers fire. This table is why a language
is "Full" vs "Good".

| Language | Complexity / nesting | Class metrics (LCOM4, god-class) | Assertion smells | Extract Method (dataflow) | Performance risk |
|----------|:---:|:---:|:---:|:---:|:---:|
| Python | ✅ | ✅ | ✅ | ✅ | ✅ |
| TypeScript / JavaScript | ✅ | ✅ | ✅ | ✅ | ✅ |
| Svelte | ✅¹⁰ | ✅ | ✅ | ✅ | ✅ |
| Java | ✅ | ✅ | ✅ | ✅ | ✅ |
| Go | ✅ | n/a¹ | ✅ | ✅ | ✅ |
| Rust | ✅ | ✅ | ✅ | ✅ | ✅² |
| C# | ✅ | ✅ | ✅ | later | ✅ |
| Kotlin | ✅ | ✅ | ✅ | later | later |
| C++ | ✅ | ✅ | ✅ | later | later |
| Dart | ✅ | n/a³ | ✅ | later | ✅ |
| Scala | ✅ | ✅ | ✅⁴ | later | ✅⁵ |
| Ruby | ✅ | ✅⁶ | ✅⁷ | later | ✅⁸ |
| Shell | ✅⁹ | n/a | n/a | n/a | n/a |

¹ Go methods attach to a type via an external receiver rather than nesting in a
class body, so class-level metrics aren't computable; Go gets the function- and
assertion-level markers.
² Rust omits `string_concat_in_loop` by design (`String::push_str` is amortized
O(1), so it would be a guaranteed false positive).
³ Dart assertion smells cover `assert` statements only; `expect()` calls have no
call-node type to key on.
⁴ Plain `assert(...)` and munit/JUnit-style `assert*` calls are counted;
ScalaTest's infix DSL (`x shouldBe y`) has no assert-prefixed callee and is not.
⁵ Rides the JVM sink lexicon (JDBC / JPA / Spring-Data interop) plus
Scala-native boundaries (`scala.io.Source`, os-lib, sttp / http4s, Slick /
doobie). Scala-specific markers: `"...".r` regex recompile in a loop and
`Await.result` / `Thread.sleep` inside a `Future`-returning def
(`blocking_sync_in_async`). Combinator iteration (`.map` / `.foreach`) is not
loop-tracked yet; loops are `while` / `do-while` / for-comprehensions.
⁶ Class size / method-count / god-class facts only. LCOM4 deliberately sits at
its "no signal" valve: idiomatic Ruby reaches state via receiver-less `@ivar`
reads and bare sibling-method calls, so the only mappable shape (`self.member`)
is too sparse to build an honest cohesion graph on. `@ivar` text grouping is a
possible follow-up.
⁷ Bare `assert` and minitest `assert_*` calls plus RSpec `expect(...)` chains
are counted; minitest's `refute_*` family is not (no assert/expect prefix), and
RSpec examples (`it ... do` blocks) are not methods, so assertion-run smells
fire on minitest-style test methods only.
⁸ **Loops include Ruby's real iteration idiom**: a combinator call with an
inline block (`.each` / `.map` / `.times` / `find_each` …) counts as a loop
scope — the block body is per-iteration, the receiver runs once, and
literal-receiver bounds (`3.times`, `[1, 2].each`, `ALL_CAPS.each`) are
constant-suppressed. ActiveRecord sinks are stratified: distinctive verbs
(`find_by` / `pluck` / `update_all` / bang persistence `create!`…) fire
ungated, `where` needs a constant-rooted receiver, and collision-prone verbs
(`find` / `first` / `count` / `save`…) need a classified db `require` — which
Zeitwerk-autoloaded Rails files rarely carry, a deliberate recall ceiling that
keeps in-memory `Registry.find(name)` lookups silent. Backticks / `system` /
`Open3` are subprocess sinks; `s += "…"` in a loop is flagged while `s << x`
(amortized append) never is.

¹⁰ Svelte rides the TypeScript dialect on all three health layers, because a
component reaches them as a TypeScript buffer. Markers therefore cover the
`<script>` blocks and markup expressions — the parts that *are* JS. Markup
structure and `<style>` carry no health signal, so a component's markers
describe its logic, not its template size.

⁹ Shell gets function-level complexity only (CCN / nesting / cognitive / NLOC).
`&&` / `||` command lists count toward CCN (`cmd || exit 1` is +1), which is
honest: shell branching is chained command lists. There are no classes,
assertions, dataflow, or perf dialect for shell.

The **performance** signal (`io_in_loop`, `string_concat_in_loop`,
`resource_construction_in_loop`, language-specific markers like Go
`defer_in_loop` and C# sync-over-async) and the **dataflow** layer (powering
Extract Method) each roll out per language in value order, degrading to silence
where a dialect isn't wired yet. Per-marker mechanics and precision hazards:
[CODE_HEALTH.md](CODE_HEALTH.md).

---

## Roadmap

| Language | Target tier | Status |
|----------|------------|--------|
| Svelte | Full | Shipped: TS projection of `<script>` + markup expressions, component symbols, `<Foo />` call edges, `$lib` / `#`-subpath resolution, SvelteKit route edges, all three health dialects. Next: `{#each}` head bindings, object-literal attributes, `.svelte.ts` rune modules |
| Dart | Good | Shipped: AST, health control-flow + class facts, perf dialect, Flutter edges. Next: riverpod/get_it dynamic hints, dataflow dialect |
| Scala | Full (health) | Shipped: complexity/class/assertion markers + perf dialect (JVM lexicon, `.r` recompile, sync-over-Future). Next: dataflow dialect, combinator (`.map`/`.foreach`) loop tracking via the shared `block_loop_body` hook Ruby established |
| Ruby | Full (health) | Shipped: complexity/class/assertion markers + perf dialect with block-iteration loops (`.each`/`.map` blocks) and the stratified ActiveRecord N+1 lexicon. Next: dataflow dialect, LCOM4 via `@ivar` grouping |
| Kotlin / C++ | Full (health) | Perf + dataflow dialects pending; everything else shipped |
| C# | Full (health) | Dataflow dialect pending; perf shipped |
| Elixir | Good | Lightweight tier shipped; AST upgrade planned (`tree-sitter-elixir` available) |
| F# | Good | Lightweight tier shipped; AST upgrade planned (`tree-sitter-f-sharp` available) |
| SQL / dbt | - | DDL symbols, dbt lineage, app-to-database contracts, health markers shipped. Next: column-level blast radius |
| Shell | - | Function symbols, `source` import edges, function-level complexity shipped. Next: shebang-based detection of extensionless executables (a traverser capability) |

---

## See also

- [architecture/language-support.md](../architecture/language-support.md), pipeline internals + how to add a language
- [CODE_HEALTH.md](CODE_HEALTH.md), code-health markers and per-language precision
- [WORKSPACES.md](../scale/WORKSPACES.md), cross-repo contracts and co-change
