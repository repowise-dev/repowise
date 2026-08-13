# Language Support

repowise parses **19 languages to a full AST**, resolves imports and call
graphs across them, and scores **13 at the Full tier** with code-health markers.
Everything else in your repo is still tracked through git history and appears in
the wiki. This page is the "what works for my language today" reference.

> **How to add a language, and how the pipeline works internally:** see
> [architecture/language-support.md](../architecture/language-support.md). Adding
> a language needs one `.scm` query file and one config entry, with no changes
> to the parser core.

**Contents:** [Tiers at a glance](#tiers-at-a-glance) ·
[Full tier](#full-tier) · [Good tier](#good-tier) · [SQL + dbt](#sql--dbt) ·
[Lightweight, Partial, Structural](#lightweight-partial-and-structural-tiers) ·
[Code-health coverage](#code-health-coverage) · [Roadmap](#roadmap) ·
[See also](#see-also)

---

## Tiers at a glance

Every language falls into one tier. The tier determines which pipeline stages
produce meaningful output.

| Tier | Languages | What works |
|------|-----------|------------|
| [**Full**](#full-tier) | Python · TypeScript · JavaScript · Svelte · Vue · Java · Kotlin · Go · Rust · C++ · C# · Scala · Ruby | AST parsing, import resolution, named bindings, call resolution, heritage, docstrings, framework-aware edges, dynamic-hint extractors, and **code-health markers** |
| [**Good**](#good-tier) | C · Swift · PHP · Dart · Object Pascal | Everything above except code-health markers (C, Swift, PHP, Object Pascal; Dart *does* get health markers). Dedicated workspace resolvers and framework edges per language, except Object Pascal, which resolves imports via the generic stem-map fallback (see [Known gaps](#object-pascal-known-gaps)) |
| [**SQL / dbt**](#sql--dbt) | `.sql` via sqlglot | Tables / views / functions / procedures as symbols with wiki pages; dbt projects get real `ref()` / `source()` lineage |
| **Shell** | `.sh` `.bash` `.zsh` | Function definitions as symbols, `source` / `.` import edges (incl. `$SCRIPT_DIR` / `dirname` / `$BATS_ROOT` idioms), and function-level code-health complexity (CCN, nesting, cognitive). No class metrics, heritage, bindings, or dead-code flagging |
| **Config / data** | OpenAPI · Protobuf · GraphQL · Dockerfile · Makefile · YAML · JSON · TOML · Terraform · Markdown | In the file tree and wiki; special handlers extract endpoints / targets where applicable |
| [**Lightweight**](#lightweight-partial-and-structural-tiers) | Elixir · Clojure · Haskell · Lean 4 · Erlang · F# · HTML | File-level import graph only (no symbols/calls). Honest file-to-file dependencies, no symbol-level claims |
| [**Partial**](#lightweight-partial-and-structural-tiers) | Luau / Roblox | AST symbols + `require()` resolution (Rojo / `.luaurc` aware); no health markers yet |
| [**Structural**](#lightweight-partial-and-structural-tiers) | Objective-C · R · Zig · Julia · Elm · OCaml · Crystal · Nim · D | Git history only (blame, hotspots, co-change). No AST parsing |

**Pipeline stage coverage:**

| Stage | Full | Good | Lightweight | Structural | Config / Data |
|-------|:----:|:----:|:-----------:|:----------:|:-------------:|
| File discovery & git history | ✅ | ✅ | ✅ | ✅ | ✅ |
| AST symbol extraction | ✅ | ✅ | - | - | - |
| Import resolution | ✅¹ | ✅ | file-level³ | - | - |
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
³ File-to-file only, no symbol resolution. Regex-extracted for every
Lightweight language except HTML, which uses the `tree-sitter-html` grammar.
Dead-code detection covers the Lightweight tier except HTML, which is never
flagged (see below).

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
| **Vue** | `.vue` | Same resolver as TS/JS, including `jsconfig`/`tsconfig` path aliases (`@/* → src/*`), directory-index components (`./Foo` → `Foo/index.vue`), and router `import()` specifiers |
| **Java** | `.java` | `import pkg.Class` / `.*` / `import static` with Maven + Gradle reactor discovery, JPMS recognition, package fan-out |
| **Kotlin** | `.kt` `.kts` | Shares the JVM workspace index with Java (cross-language resolution); `.kt` under `src/main/java` recognised |
| **Go** | `.go` | `import "path"` with multi-module `go.mod` discovery; a package import fans out to every file in the package |
| **Rust** | `.rs` | `use crate::` / `super::` / `self::` with `Cargo.toml` |
| **C++** | `.cpp` `.cc` `.cxx` `.h` `.hpp` `.hxx` | `#include` via `compile_commands.json` + CMake / Bazel workspace header maps, header↔implementation pairing |
| **C#** | `.cs` | `using` / `global using` / `using static` / aliases with `.csproj` / `.sln` resolution; MSBuild project graph; `partial` class linking |
| **Scala** | `.scala` | `import pkg.Foo`, brace/wildcard/package imports via the shared JVM index (cross-language with Java/Kotlin); SBT / Mill build parsing as fallback (partial import resolution¹) |
| **Ruby** | `.rb` | `require` / `require_relative` with `$LOAD_PATH` probing, Gemfile externals, RSpec mirror edges, Rails / Zeitwerk autoloading |

All thirteen also support three-tier call resolution (same-file, cross-file,
global stem match) and docstring extraction (Python, Ruby comments, JSDoc,
GoDoc, Rustdoc, Javadoc, Scaladoc, Doxygen, XML doc).

**Single-file components** (`.svelte`, `.vue`) are three languages in one file,
so they get a shared projection rather than a grammar of their own. A markup
grammar locates the `<script>` blocks and the markup expressions; everything
else (markup, `<style>`) is blanked to spaces with newlines preserved, and the
result is parsed as TypeScript at **byte-identical offsets**. So a component
reuses the TypeScript queries, config, and all three health dialects verbatim,
and every line number points at the real source file. Only the region-location
step differs per language, behind a small locator registry in `sfc_source.py`.

Vue has no grammar on PyPI, but `tree-sitter-html` parses an SFC cleanly —
`<template>`, `<script>` and `<style>` are just elements to it — so one
dependency covers both Vue and plain HTML.

| | Svelte | Vue |
|---|---|---|
| Script blocks | `<script>`, `<script context="module">` | `<script>`, `<script setup>` |
| Markup expressions | `{expr}`, `on:click={inc}`, `{#if}` heads | `:class="c"`, `@click="inc"`, `v-if="ok"`, `{{ interp }}` |
| Expression fence | the surrounding `{` `}` | the surrounding attribute quotes |
| Skipped binding forms | `{#each x as y}`, `{#await}` | `v-for="x in xs"`, `v-slot` / `#default` |

Three pieces sit on top for both:

- the **component itself** becomes a class-kind symbol named after the file
  (`Button.svelte` → `Button`), since nothing in the source names it. Vue
  normalises the stem the same way it normalises a tag, so `warningBar.vue`,
  `back-to-top.vue` and `Logo/index.vue` declare `WarningBar`, `BackToTop` and
  `Logo` — which is what a parent actually imports and writes;
- **`<Foo />` in markup** mints a call edge on `Foo`, the same way `tsx.scm`
  treats a JSX element. Framework intrinsics never do: Svelte's `svelte:*`
  namespace, and Vue's `<KeepAlive>` / `<Transition>` / `<RouterView>` in
  either the PascalCase or kebab spelling;
- **markup expressions are kept**, so a handler referenced only from
  `on:click={inc}` or `@click="inc"` still carries an edge instead of reading
  as dead code.

Deliberate ceilings. Binding forms (the table above) *parse* as JS but mean
something else, so they are skipped — a parse that succeeds with the wrong
meaning is worse than a skip. Object-literal attributes (`use:action={{ a, b }}`,
`#default="{ row }"`) read as a block at statement position and are dropped. A
component's props are set by the parent as markup attributes and never imported
by name, so the unused-export pass is suppressed for both languages — the
alternative flags every prop, and every component, in the repo.

One ceiling is Vue-specific: an Options-API member spelled
`foo: function () {}` or `foo: () => {}` is not captured, because
`typescript.scm` has no pattern for a `pair` with a function value. The
shorthand `foo() {}` spelling **is** captured, which covers 1,588 of 1,592
member functions (99.7%) across the 275 Options-API files in the validation
corpus. The remaining 0.3% is a general TS/JS object-literal gap, not a Vue
one, so lifting it belongs in `typescript.scm`.

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
| **Object Pascal** | `.pas` `.pp` `.dpr` `.dpk` `.lpr` `.inc` | `uses UnitA, UnitB;` resolved via the generic unit-name → file-stem fallback (no dedicated resolver); `.dpr`/`.dpk`/`.lpr` project files as entry points |

### Object Pascal known gaps

Delphi (`.pas`/`.dpr`/`.dpk`) and Free Pascal/Lazarus (`.pas`/`.pp`/`.lpr`) via
`tree-sitter-pascal`. Newest AST-parsed language in this tier, so its ceilings
are less battle-tested than C/Swift/PHP/Dart's:

- **Type kind collapses to `"class"`.** `record` / `interface` / class-helper /
  enum / plain type alias are all reported as `kind="class"` — the query
  captures the declaration but not which of the five forms it is.
- **`extends`/`implements` heritage split is best-effort.** Delphi's
  `class(TBase, IFoo, IBar)` ancestor list doesn't itself distinguish a base
  class from an implemented interface; the heritage extractor infers it from
  naming convention (`I`-prefixed identifiers), which is the real-world Delphi
  convention but not a language guarantee.
- **No dedicated import resolver.** Unlike C/Swift/PHP/Dart, `uses` clauses
  resolve through the same generic unit-name → file-stem fallback as the
  Lightweight tier, rather than a project-file-aware resolver — accurate for
  the near-universal "unit name equals file stem" convention, wrong when it
  doesn't hold.
- **No code-health markers yet** (complexity, duplication, dataflow dialects
  aren't registered for Pascal).
- **One known grammar gap left unhandled deliberately:** an anonymous
  `array[...] of record ... end` element type has no tree-sitter-pascal rule
  and degrades to a wrong `parent_name` for whatever the same class declares
  afterward — contained to that one class, not fixed, since a correct fix
  needs a nesting-aware scanner for one construct seen once in the validation
  corpus. See `prepare_pascal_source` in `ingestion/parser_helpers.py`.

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

**Lightweight** (Elixir, Clojure, Haskell, Lean 4, Erlang, F#, HTML), no symbol
extraction, but a real file-level import graph: import statements are extracted
per-language and resolved against a declared module-name index. The knowledge
graph runs in flow/sparse mode on the result: honest file-to-file dependencies,
no symbol-level claims. F# additionally honours the fsproj `<Compile Include>`
compile-order spine. All of these use a regex tier except HTML, which uses the
`tree-sitter-html` grammar repowise already ships for Vue.

**HTML** (`.html` / `.htm`) is import-tier *only*, and deliberately so: HTML has
no functions, classes or calls, so there are no symbols to claim. What it does
carry is `<script src>` and `<link href>`, which become file-level edges —
including the one every Vite/webpack SPA depends on, `index.html` →
`src/main.ts`. References are resolved as document- or root-relative asset
paths, never as module specifiers: there is no extension inference and no
`index.*` lookup, because `src="./app"` in a browser fetches a file literally
named `app`. A root-relative `/src/main.tsx` is anchored first at the
referencing page's own directory (the bundler convention), then at its
`public/`, then by unique path suffix; a tie yields no edge rather than a
guessed one. CDN and `data:` references are external and mint no edge.

`.html` files are **never flagged as dead code**. Whether a page is reachable
is not statically decidable — a server serves it, a human navigates to it, a
build copies it — and checked-in generated HTML is everywhere. Their outbound
edges still anchor everything they reference.

The known ceiling is **template dialects**. Django/Jinja, Go templates, ERB,
Handlebars, Blade, Thymeleaf and Angular's `*ngIf` are invisible to an HTML
parser: `{% extends "base.html" %}` is plain text, so such a file parses
cleanly and yields nothing. Measured on the validation corpus, 744 of 749
template-dialect files (99.3%) produce no edges at all. Covering them needs a
per-dialect regex tier gated on a framework manifest — a different mechanism,
not yet built.

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
| Vue | ✅¹⁰ | ✅ | ✅ | ✅ | ✅ |
| Java | ✅ | ✅ | ✅ | ✅ | ✅ |
| Go | ✅ | n/a¹ | ✅ | ✅ | ✅ |
| Rust | ✅ | ✅ | ✅ | ✅ | ✅² |
| C# | ✅ | ✅ | ✅ | later | ✅ |
| Kotlin | ✅ | ✅ | ✅ | later | ✅¹¹ |
| C++ | ✅ | ✅ | ✅ | ✅ | ✅¹² |
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

¹⁰ Svelte and Vue ride the TypeScript dialect on all three health layers, because a
component reaches them as a TypeScript buffer. Markers therefore cover the
`<script>` blocks and markup expressions — the parts that *are* JS. Markup
structure and `<style>` carry no health signal, so a component's markers
describe its logic, not its template size.

¹¹ Kotlin rides the JVM sink lexicon (JDBC / JPA / Spring-Data interop) plus
Kotlin-native boundaries (JetBrains Exposed, `File`-only `kotlin.io`
extensions). **Loops include combinator iteration**: a call with a trailing
lambda whose method is a full-iteration combinator (`forEach` / `map` /
`filter` / `fold` …) is a loop scope via the shared `block_loop_body` hook Ruby
established — scope functions (`let` / `apply` / `run`) and early-exit searches
(`firstOrNull`) deliberately are not. `suspend` is a modifier *token*, so
`blocking_sync_in_async` fires on `runBlocking` / `Thread.sleep` inside a
`suspend fun`. Three deliberate recall ceilings, each set after a false
positive on a real corpus: bare HTTP verbs are excluded (they are the
route-registration DSL of every Kotlin web framework), the generic
`kotlin.io` stream verbs `readText` / `writeText` / `readBytes` / `copyTo` are
excluded (`kotlinx-io` reuses them on in-memory buffers), and the ambiguous db
stratum drops `find` / `get` / `count` (they are stdlib collection
combinators here, unlike in Java). A regex pattern containing a string
template is not reported, because it is not hoistable.

¹² C++ is deliberately narrow, and omits three markers other languages carry
because each would be a guaranteed false positive: `string_concat_in_loop`
(`std::string::operator+=` appends in place into a geometrically-grown buffer —
amortized O(1), the same reason Rust omits it), `resource_construction_in_loop`
(a loop-built `std::ifstream` is opened over a per-iteration *path*, and
`std::thread` in a loop is how a thread pool is built), and
`blocking_io_under_lock` (an RAII `lock_guard` holds to the end of the
*enclosing* block, so no node's body is the held region). What remains:
`io_in_loop` over POSIX / `std::filesystem` / libcurl / sqlite3 / MySQL /
libpq entry points, `regex_compile_in_loop` on a constant-pattern `std::regex`,
and `lock_in_loop`. Two ceilings: a C free function classifies only when
*truly unqualified* (a namespaced call merely sharing a POSIX name — `json::accept`,
`std::fprintf` — never does), and the socket verbs that double as plausible
member names (`send` / `recv` / `connect` / `bind` / `listen` / `accept`) are
excluded, because an implicit-`this` member call is spelled identically. C has
no `LanguageNodeMap` at all, so it reaches no dialect despite sharing the
grammar.

¹³ **Kotlin dataflow is blocked on tree-sitter-kotlin, not merely unscheduled.**
The def/use dialect itself would be routine; the CFG builder and the Extract
Method slicer are what the grammar defeats, in four independent places:
`function_declaration` labels no `body` field (and wraps its block in a
`function_body` sibling, so the existing single-child unwrap misses it);
`for_statement` / `while_statement` label no `body` field either;
`if_expression` labels no `alternative` field and has no `else_clause` node, so
an `else` body would be silently dropped from the CFG; and a bare `break` /
`continue` parses as a plain `identifier`, with no node type to key on. That
last one is the blocker that matters: the slicer refuses any span containing a
jump, so invisible jumps would let it propose an Extract Method that silently
changes control flow — a wrong suggestion, not a missing one. Kotlin therefore
stays at "no dataflow signal", which is the correct degradation. Reviving this
needs either a grammar upgrade or a text-based jump seam plus positional
body/else resolution in the CFG core.

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
| Vue | Full | Shipped: TS projection of `<script>` / `<script setup>` + template expressions, component symbols with tag-consistent naming, `<Foo />` call edges, alias + directory-index + dynamic-`import()` resolution, all three health dialects. Next: Options-API `pair`-function members, `v-for` head bindings |
| Svelte | Full | Shipped: TS projection of `<script>` + markup expressions, component symbols, `<Foo />` call edges, `$lib` / `#`-subpath resolution, SvelteKit route edges, all three health dialects. Next: `{#each}` head bindings, object-literal attributes, `.svelte.ts` rune modules |
| Dart | Good | Shipped: AST, health control-flow + class facts, perf dialect, Flutter edges. Next: riverpod/get_it dynamic hints, dataflow dialect |
| Scala | Full (health) | Shipped: complexity/class/assertion markers + perf dialect (JVM lexicon, `.r` recompile, sync-over-Future). Next: dataflow dialect, combinator (`.map`/`.foreach`) loop tracking via the shared `block_loop_body` hook Ruby established |
| Ruby | Full (health) | Shipped: complexity/class/assertion markers + perf dialect with block-iteration loops (`.each`/`.map` blocks) and the stratified ActiveRecord N+1 lexicon. Next: dataflow dialect, LCOM4 via `@ivar` grouping |
| Kotlin | Full (health) | Shipped: complexity/class/assertion markers + perf dialect (JVM lexicon, Exposed, combinator loops via `block_loop_body`, `suspend` sync-in-async). Dataflow is **blocked on the grammar**, not unstarted — see ¹³ |
| C++ | Full | Shipped: complexity/class/assertion markers, perf dialect (POSIX / `std::filesystem` / sqlite3 sinks, constant-pattern `std::regex` recompile, `lock_in_loop`), and the dataflow dialect powering Extract Method |
| C# | Full (health) | Dataflow dialect pending; perf shipped |
| Elixir | Good | Lightweight tier shipped; AST upgrade planned (`tree-sitter-elixir` available) |
| F# | Good | Lightweight tier shipped; AST upgrade planned (`tree-sitter-f-sharp` available) |
| SQL / dbt | - | DDL symbols, dbt lineage, app-to-database contracts, health markers shipped. Next: column-level blast radius |
| Shell | - | Function symbols, `source` import edges, function-level complexity shipped. Next: shebang-based detection of extensionless executables (a traverser capability) |
| HTML | Lightweight | Shipped: `<script src>` / `<link href>` edges with document-, `public/`- and root-relative resolution; never dead-code flagged. Stays import-tier — HTML has no symbols. Next: a regex import tier for template dialects (Django/Jinja, Go templates, ERB, Handlebars), gated on a framework manifest |

---

## See also

- [architecture/language-support.md](../architecture/language-support.md), pipeline internals + how to add a language
- [CODE_HEALTH.md](CODE_HEALTH.md), code-health markers and per-language precision
- [WORKSPACES.md](../scale/WORKSPACES.md), cross-repo contracts and co-change
