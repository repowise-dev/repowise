# Language Support

**25 languages parsed to a full AST · 39 on the five-rung ladder ·
framework-aware across all of them.** "Do you support X" has five useful answers
rather than two, so every language lands on a rung and the rung says what it
buys you. Everything else in your repo still appears in the wiki and is tracked
through git history. This page is the "what works for my language today"
reference.

<p>
  <strong>Full tier &nbsp;</strong>
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript" />
  <img src="https://img.shields.io/badge/JavaScript-F7DF1E?style=flat-square&logo=javascript&logoColor=black" alt="JavaScript" />
  <img src="https://img.shields.io/badge/Svelte-FF3E00?style=flat-square&logo=svelte&logoColor=white" alt="Svelte" />
  <img src="https://img.shields.io/badge/Vue-42B883?style=flat-square&logo=vuedotjs&logoColor=white" alt="Vue" />
  <img src="https://img.shields.io/badge/Java-ED8B00?style=flat-square&logo=openjdk&logoColor=white" alt="Java" />
  <img src="https://img.shields.io/badge/Kotlin-7F52FF?style=flat-square&logo=kotlin&logoColor=white" alt="Kotlin" />
  <img src="https://img.shields.io/badge/Go-00ADD8?style=flat-square&logo=go&logoColor=white" alt="Go" />
  <img src="https://img.shields.io/badge/Rust-000000?style=flat-square&logo=rust&logoColor=white" alt="Rust" />
  <img src="https://img.shields.io/badge/C++-00599C?style=flat-square&logo=cplusplus&logoColor=white" alt="C++" />
  <img src="https://img.shields.io/badge/C%23-512BD4?style=flat-square&logo=csharp&logoColor=white" alt="C#" />
  <img src="https://img.shields.io/badge/Scala-DC322F?style=flat-square&logo=scala&logoColor=white" alt="Scala" />
  <img src="https://img.shields.io/badge/Ruby-CC342D?style=flat-square&logo=ruby&logoColor=white" alt="Ruby" />
</p>
<p>
  <strong>Good tier &nbsp;</strong>
  <img src="https://img.shields.io/badge/C-A8B9CC?style=flat-square&logo=c&logoColor=black" alt="C" />
  <img src="https://img.shields.io/badge/Swift-F05138?style=flat-square&logo=swift&logoColor=white" alt="Swift" />
  <img src="https://img.shields.io/badge/PHP-777BB4?style=flat-square&logo=php&logoColor=white" alt="PHP" />
  <img src="https://img.shields.io/badge/Dart-0175C2?style=flat-square&logo=dart&logoColor=white" alt="Dart" />
  <img src="https://img.shields.io/badge/Delphi-EE1F35?style=flat-square&logo=delphi&logoColor=white" alt="Object Pascal / Delphi" />
  <img src="https://img.shields.io/badge/GDScript-478CBF?style=flat-square&logo=godotengine&logoColor=white" alt="GDScript / Godot" />
  <img src="https://img.shields.io/badge/VB.NET-945DB7?style=flat-square&logo=dotnet&logoColor=white" alt="VB.NET" />
  <img src="https://img.shields.io/badge/Elixir-6E4A7E?style=flat-square&logo=elixir&logoColor=white" alt="Elixir" />
  <img src="https://img.shields.io/badge/F%23-378BBA?style=flat-square&logo=fsharp&logoColor=white" alt="F#" />
  <img src="https://img.shields.io/badge/Objective--C-438EFF?style=flat-square&logo=apple&logoColor=white" alt="Objective-C" />
  &nbsp;<strong>· Partial &nbsp;</strong>
  <img src="https://img.shields.io/badge/Luau-00A2FF?style=flat-square&logo=lua&logoColor=white" alt="Luau" />
  <img src="https://img.shields.io/badge/Razor-512BD4?style=flat-square&logo=blazor&logoColor=white" alt="Razor / Blazor" />
</p>

**Contents:** [Tiers](#tiers) ·
[What the pipeline gives each tier](#what-the-pipeline-gives-each-tier) ·
[Why these graphs are different](#why-these-graphs-are-different) ·
[Full tier](#full-tier) · [Good tier](#good-tier) ·
[Beyond code files](#beyond-code-files) ·
[Code-health coverage](#code-health-coverage) ·
[Known ceilings](#known-ceilings) · [Roadmap](#roadmap)

---

## Tiers

Every language lands in one tier, and the tier decides which pipeline stages
produce meaningful output.

| Tier | Languages | What you get |
|------|-----------|--------------|
| **Full** (13) | Python · TypeScript · JavaScript · Svelte · Vue · Java · Kotlin · Go · Rust · C++ · C# · Scala · Ruby | The whole pipeline: AST symbols, import resolution, a resolved call graph, heritage, docstrings, framework edges, **and code-health markers** |
| **Good** (10) | C · Swift · PHP · Dart · Object Pascal · GDScript · VB.NET · Elixir · F# · Objective-C | Everything above except the full health suite. Dart and Object Pascal *do* get health markers; C, Swift, PHP, GDScript, VB.NET, Elixir, F# and Objective-C don't yet. GDScript has a dedicated import resolver and Godot-specific framework edges but no named bindings (see [Known gaps](../architecture/language-support.md#gdscript--godot)) |
| **Partial** (2) | Luau / Roblox · Razor / Blazor | Luau: AST symbols and `require()` resolution (Rojo / `.luaurc` aware), no health markers yet. Razor: a component symbol per file, call edges from `@code` blocks and component tags, C# health markers; no import resolution yet |
| | | ⎯⎯ *tree-sitter parsing stops here. The rungs below are derived from git and imports, not from an AST.* ⎯⎯ |
| **Lightweight** (6) | Clojure · Haskell · Lean 4 · Erlang · HTML · QML | A real file-to-file import graph, no symbol-level claims |
| **Structural** (8) | R · Zig · Julia · Elm · OCaml · Crystal · Nim · D | Git history only: blame, hotspots, co-change. No AST parsing |

The first three rungs are the **25 languages parsed to a full AST**; all five are
the **39** on the ladder. Both numbers are worth stating and neither is worth
stating alone, so if you only take one thing from this page, take the rung your
language sits on rather than either count.

[**SQL / dbt**](#sql--dbt) and [**shell**](#shell) sit outside this ladder on
purpose: each has a coverage shape the tiers cannot describe. SQL is parsed by
sqlglot rather than tree-sitter and gets symbols, wiki pages and health markers
but no call graph, with import edges only inside a dbt project. Shell gets
symbols, `source` edges and function-level complexity, but reachability is
meaningless for a script invoked by name. See
[Beyond code files](#beyond-code-files), along with
[config and data formats](#config-and-data).

## What the pipeline gives each tier

| Stage | Full | Good | Partial | Lightweight | Structural |
|-------|:----:|:----:|:-------:|:-----------:|:----------:|
| File discovery & git history | ✅ | ✅ | ✅ | ✅ | ✅ |
| AST symbol extraction | ✅ | ✅ | ✅ | — | — |
| Import resolution | ✅ | ✅ | Luau | file-level | — |
| Call graph edges | ✅ | ✅ | ✅ | — | — |
| Heritage (extends / implements) | ✅ | ✅ | — | — | — |
| Named bindings | ✅ | ✅ | — | — | — |
| Code-health markers | ✅ | Dart, Pascal | Razor | — | — |
| Dead code detection | ✅ | ✅ | ✅ | ✅ | ✅ |
| Semantic search & wiki pages | ✅ | ✅ | ✅ | ✅ | ✅ |

Scala's import resolution is partial: it shares the JVM index with Java and
Kotlin and falls back to parsing SBT / Mill build files. Every other Full and
Good language resolves imports outright. On the Partial rung the two languages
split: Luau resolves `require()` and has no health markers, Razor has C#
health markers and no import edges yet.

---

## Why these graphs are different

Plenty of tools will hand you a call graph. Three things separate a graph you
can act on from a picture of arrows. This is the short version; the full story,
with the complete origin table and worked examples, is in
[GRAPH.md](GRAPH.md).

### Every call edge says how it was resolved, and how much to trust it

Most graphs give you an unlabelled arrow. `A calls B`, but was `B` the only
match in the entire repo, or was it right there in the same file? Those are
wildly different claims, and a tool that renders them identically is asking you
to trust its weakest guess as much as its strongest fact.

Every `calls` edge repowise emits carries a **resolution origin** from a closed
vocabulary, and every origin has exactly one confidence:

| Origin | Confidence | What it means |
|--------|:---:|---|
| `same_file` | 0.95 | Defined in the calling file. A certainty |
| `self_scope` | 0.95 | `self` / `this`, a method on the caller's own class |
| `import_scoped` | 0.90 | The name was imported from the file that defines it |
| `receiver_typed_import` | 0.88 | The receiver's type was read off its declaration, then found in an imported file |
| `receiver_global` | 0.75 | The `(class, method)` pair exists *somewhere* in the repo |
| `global_unique` | 0.50 | The name is unique repo-wide. **A guess, and labelled as one** |

That is six of 29. You can filter a graph by confidence, and both
the MCP tools and the web UI surface which origin produced an edge, so an agent
reading an execution flow can tell a fact from an inference instead of treating
both as source.

### A call on a variable resolves by typing the variable

`user.save()` is only a useful edge if something worked out what `user` is. The
naive approach matches `save` against every method named `save` in the repo and
picks one, which is how graphs end up confidently wrong.

repowise instead reads the receiver's **declaration** and resolves the method on
that type. It covers the receiver shapes that actually occur:

- **locals and parameters**: `var repo = new UserRepo()`, `fun f(r: UserRepo)`
- **fields**: a call on `this.repo`, typed from the enclosing class
- **freshly constructed receivers**: `new Foo().bar()`
- **the method's own receiver**: Go's `func (s *Server) handle()`
- **framework-retyped symbols**: `@shared_task def add` makes `add` a `Task`,
  so `add.s()` resolves to `Task::s`

Shipped for **Java, C#, Python, Go, Kotlin and Swift**. Each language was gated
on measured precision before it shipped, and the ones that failed the gate are
recorded as unsupported rather than shipped loose.

### It reports silence instead of guessing

Every capability degrades to *no signal* rather than a wrong one, and each
degradation is deliberate:

- **An execution flow says why it stopped.** A trace that just ends reads the
  same whether execution really ends there or the walker ran out of things it
  could follow. Six terminations are named separately: a real end, a cycle, an
  exhausted hop budget, an all-excluded successor set, a confidence-filtered one,
  and calls that failed to resolve. Where a confidence floor stopped the walk, it
  reports which origins it declined.
- **A `calls` edge means the callee is callable.** Edges pointing at properties,
  constants and modules were withdrawn and re-minted as `references`: "something
  holds a handle to this", which is enough to make deleting it unsafe and not
  enough to claim it is invoked.
- **Framework wiring is its own edge type.** A pytest fixture injection or a
  Spring `@Autowired` field is a `framework_binds` edge, never a `calls` edge.
  Nothing there is a call the parser could have seen, and letting it read as one
  would put an inferred hop into an execution flow as if it were source.
- **An unmapped language produces no findings, not bad ones.** A language reaches
  a health dialect or it stays silent. There is no "best effort" middle.

---

## Full tier

Complete pipeline coverage: AST parsing, import resolution, call resolution,
named bindings, heritage, docstrings, framework-aware edges, dynamic-hint
extractors and code-health markers.

| Language | Extensions | Import resolution |
|----------|-----------|--------------|
| **Python** | `.py` `.pyi` | Source-root-aware module index (`src/`, monorepo `packages/*/src`, PEP 420), `__init__.py` re-export barrels |
| **TypeScript** | `.ts` `.tsx` | ESM / `require()`, tsconfig path aliases, npm/yarn/pnpm workspaces, `export * from` barrels |
| **JavaScript** | `.js` `.jsx` `.mjs` `.cjs` | `import` / `require()` including CommonJS re-export shapes and member picks |
| **Svelte** | `.svelte` | The TS/JS resolver plus SvelteKit's `$lib` and Node `#`-prefixed subpath imports |
| **Vue** | `.vue` | The TS/JS resolver plus `jsconfig`/`tsconfig` aliases, directory-index components, router `import()` specifiers |
| **Java** | `.java` | `import` / `.*` / `import static` with Maven + Gradle reactor discovery, JPMS, package fan-out |
| **Kotlin** | `.kt` `.kts` | Shares the JVM workspace index with Java, so resolution is cross-language |
| **Go** | `.go` | Multi-module `go.mod` discovery; a package import fans out to every file in the package |
| **Rust** | `.rs` | `use crate::` / `super::` / `self::` with `Cargo.toml` |
| **C++** | `.cpp` `.cc` `.cxx` `.h` `.hpp` `.hxx` `.inl` `.ipp` `.tpp` | `#include` via `compile_commands.json` plus CMake / Bazel header maps, header↔implementation pairing |
| **C#** | `.cs` | `using` / `global using` / aliases via `.csproj` / `.sln`, MSBuild project graph, `partial` class linking |
| **Scala** | `.scala` | The shared JVM index (cross-language with Java/Kotlin), SBT / Mill build parsing as fallback |
| **Ruby** | `.rb` | `require` / `require_relative` with `$LOAD_PATH` probing, Gemfile externals, Rails / Zeitwerk autoloading |

All thirteen also get docstring extraction: Python, Ruby comments, JSDoc,
GoDoc, Rustdoc, Javadoc, Scaladoc, Doxygen and XML doc.

### Framework-aware edges

Routes connect to handlers, DI registrations to implementations, ORM entities to
their relationships:

| Language | Frameworks |
|----------|-----------|
| Python | Django, FastAPI, Flask, Celery, pytest fixtures |
| Ruby | Rails (routes → controller actions, Zeitwerk), RSpec mirror edges |
| Java / Kotlin | Spring (stereotypes, `@RequestMapping`, Spring Data, `@Bean`), Jakarta / JPA, Quarkus, Micronaut, Android manifest |
| C# | ASP.NET (attribute + minimal API), EF Core, gRPC-dotnet, host-builder extensions, CommunityToolkit MVVM |
| Go | net/http, gin, echo, chi, gRPC server registration |
| Rust | Axum, Actix route → handler |
| JS / TS / Svelte | Next.js App Router, Hono / Fastify / Koa / Elysia, Remix / SvelteKit / Astro, tRPC, Express / NestJS |
| C++ | GoogleTest, Catch2, Boost.Test, doctest, Google Benchmark, libFuzzer |

The dead-code analyzer knows each ecosystem's entry points, generated-file
conventions and never-flag globs, so build products and framework-invoked code
are not reported as unreachable.

### Single-file components (Svelte, Vue)

A `.svelte` or `.vue` file is projected into TypeScript at **byte-identical
offsets**, so a component reuses the TypeScript queries, config and all three
health dialects verbatim and every line number points at the real source file.
The component itself becomes a symbol named after the file, `<Foo />` in markup
mints a call edge, and a handler referenced only from `on:click={inc}` or
`@click="inc"` carries an edge instead of reading as dead code.

### Razor / Blazor markup (`.razor`, `.cshtml`)

Razor has the same shape, but with no usable grammar available the locator
byte-scans `@code { }`, `@functions { }` and `@{ }` interiors into a C# buffer
at byte-identical offsets, so the C# queries and health markers apply verbatim
and PascalCase markup tags mint component call edges. It sits on the Partial
rung because `@code` members land as call edges rather than symbols and the
directives are blanked, so a Razor file carries no import edges yet.

---

## Good tier

AST parsing, symbol extraction, import resolution, call resolution, named
bindings and heritage, with a dedicated workspace resolver per language.

| Language | Extensions | Import resolution |
|----------|-----------|--------------|
| **C** | `.c` | `#include` via `compile_commands.json` (shares the C++ grammar) |
| **Swift** | `.swift` | SPM `Package.swift` target → directory mapping, intra-module type references, `@main` entry points |
| **PHP** | `.php` | `use Foo\Bar\Baz` with composer.json PSR-4 longest-prefix resolution; Laravel, TYPO3 edges |
| **Dart** | `.dart` | `import` / `export` / `part` URIs, `package:` via every `pubspec.yaml`, Flutter route tables and `runApp()` edges. **Health markers included** |
| **Object Pascal** | `.pas` `.pp` `.dpr` `.dpk` `.lpr` `.inc` | `uses` clauses via the generic unit-name → file-stem fallback; project files as entry points. **Health markers included** |
| **GDScript** | `.gd` | `preload(...)` / `load(...)` / `extends "res://..."` resolved as absolute paths from the nearest `project.godot`, so a repo holding many Godot projects keeps each project's `res://` namespace separate, plus scene, autoload and `class_name` edges (see [GDScript / Godot](../architecture/language-support.md#gdscript--godot)) |
| **VB.NET** | `.vb` | `Imports` through the same MSBuild project index C# uses: `.vbproj` / `.sln` parsing, `<RootNamespace>`-aware namespace lookup, NuGet package references |
| **Elixir** | `.ex` `.exs` | `alias` / `import` / `require` / `use` against a `defmodule` index, with the Mix `lib/foo/bar.ex` → `Foo.Bar` convention as the fallback; `alias Foo.{Bar, Baz}` names both modules (no heritage: `use` and `@behaviour` are not inheritance) |
| **F#** | `.fs` `.fsx` `.fsi` | `open` / `open type` against a declared-name index (namespace/module headers), plus the fsproj `<Compile Include>` compile order. `.fsi` signature files contribute imports only, no symbols. A layout that shares one namespace across many single-file projects leaves most `open` targets ambiguous by design, so unused-export findings there sit at the review tier (0.4) rather than being asserted |
| **Objective-C** | `.m` `.mm` `.h` | `#import` / `#include` through the generic header-stem index, which covers the one-class-per-file convention; a framework import names no in-repo file and resolves to nothing |

---

## Beyond code files

### SQL + dbt

Parsed by a dedicated sqlglot handler (multi-dialect, error-tolerant) rather
than tree-sitter.

- **DDL symbols**: `CREATE TABLE` / `VIEW` become class-kind symbols with their
  columns in the signature; `CREATE FUNCTION` / `PROCEDURE` become function-kind
  symbols. Both get wiki pages and `get_symbol` lookups. Set `sql_dialect` in
  config for dialect-specific syntax. Any parse problem degrades the file to
  passthrough, never a crash, never a guess.
- **dbt lineage**: `{{ ref('model') }}` and `{{ source(...) }}` become real
  import edges, so model-level lineage, hotspots, co-change, ownership and
  communities all fall out free.
- **App-to-database contracts** (workspace mode), table *providers* (DDL,
  Alembic, ORM entities) pair with table *consumers* (SQL literals in app code)
  on the Live System Map. See [WORKSPACES.md](../scale/WORKSPACES.md).
- **Health markers**: stored routines get cyclomatic complexity, plus
  `sql_select_star`, `sql_update_delete_without_where` and `sql_cartesian_join`.
  All of them are **uncalibrated by construction** (no defect corpus covers
  procedural SQL), so they surface as findings and never move the defect
  headline score.

SQL is not placed in a tier because it would misreport in both directions:
Partial would understate the health markers, and any higher tier would claim a
call graph and heritage that a DDL file does not have. Outside a dbt project,
`.sql` files get symbols and pages but no import edges at all.

### Shell

`.sh` / `.bash` / `.zsh`. Function definitions become symbols, `source` / `.`
statements become import edges, and calls to functions defined in the same or a
sourced file resolve to call edges; external binaries like `grep` mint nothing.
Resolution covers literal paths plus the common directory-anchor idioms
(`$SCRIPT_DIR/x.sh`, `$(dirname "$0")/x.sh`, `$BATS_ROOT/lib/x.sh`); genuinely
dynamic paths stay external. Shell also gets **function-level complexity**, with
`&&` / `||` command lists counted toward CCN. No class metrics, heritage or
dead-code flagging: shell scripts are invoked by name, so static reachability is
meaningless.

### Lightweight tier

Clojure, Haskell, Lean 4, Erlang and HTML get a real file-level import graph:
imports extracted per language and resolved against a declared module-name
index. The knowledge graph runs in flow/sparse mode on the result: honest
file-to-file dependencies, no symbol-level claims.

**QML** joins the lightweight tier with imports only: module specs resolve
against a `qmldir`-declared module index and quoted references resolve relative
to the importing file. `.qml` files are **never flagged as dead code**, because
a component is instantiated by type name and loaded by the Qt runtime through
qrc, `Loader` and `qmlRegisterType`, none of which is an import.

**HTML** is import-tier on purpose. It has no functions, classes or calls, so
there are no symbols to claim, but its `<script src>` and `<link href>` become
file-level edges, including the one every Vite/webpack SPA depends on:
`index.html` → `src/main.ts`. References resolve as document- or root-relative
asset paths, never as module specifiers, and a tie yields no edge rather than a
guessed one. `.html` files are **never flagged as dead code**: whether a page is
reachable is not statically decidable.

### Config and data

OpenAPI, Protobuf, GraphQL, Dockerfile, Makefile, YAML, JSON, TOML, Terraform and
Markdown appear in the file tree and the wiki, with special handlers extracting
endpoints and targets where applicable.

Godot resource files sit here too: `.tscn` / `.tres` / `.escn`,
`project.godot` and an addon's `plugin.cfg` carry no symbols, but the paths
they name are how a Godot project reaches its scripts, so they are indexed and
read for those paths. See
[GDScript / Godot](../architecture/language-support.md#gdscript--godot).

---

## Code-health coverage

Health markers run off a per-language walker map that is **independent** of
`.scm` parsing: a language can parse perfectly for the graph and still need this
map before markers fire. This table is why a language is Full rather than Good.

| Language | Complexity / nesting | Class metrics | Assertion smells | Extract Method | Performance risk |
|----------|:---:|:---:|:---:|:---:|:---:|
| Python | ✅ | ✅ | ✅ | ✅ | ✅ |
| TypeScript / JavaScript | ✅ | ✅ | ✅ | ✅ | ✅ |
| Svelte · Vue | ✅ | ✅ | ✅ | ✅ | ✅ |
| Java | ✅ | ✅ | ✅ | ✅ | ✅ |
| Go | ✅ | n/a | ✅ | ✅ | ✅ |
| Rust | ✅ | ✅ | ✅ | ✅ | ✅ |
| C++ | ✅ | ✅ | ✅ | ✅ | ✅ |
| C# | ✅ | ✅ | ✅ | later | ✅ |
| Kotlin | ✅ | ✅ | ✅ | blocked | ✅ |
| Scala | ✅ | ✅ | ✅ | later | ✅ |
| Ruby | ✅ | ✅ | ✅ | later | ✅ |
| Dart | ✅ | n/a | ✅ | later | ✅ |
| Object Pascal | ✅ | n/a | later | later | n/a |
| Razor | ✅ | n/a | n/a | later | ✅ |
| Shell | ✅ | n/a | n/a | n/a | n/a |

Every cell is a deliberate call, not an oversight: a language reaches a dialect
or it stays silent, and an `n/a` records a metric the language cannot carry
rather than one nobody got to. Kotlin's Extract Method is **blocked on the
grammar**, not unscheduled.

Per-marker mechanics, every per-language precision ceiling and the reasoning
behind each `n/a`: [CODE_HEALTH.md](CODE_HEALTH.md) and
[architecture/language-support.md](../architecture/language-support.md).

---

## Known ceilings

Stated plainly, because a ceiling you know about is worth more than a claim you
cannot check.

- **Template dialects are invisible.** Django/Jinja, Go templates, ERB,
  Handlebars, Blade and Thymeleaf parse as HTML and yield nothing.
- **Svelte and Vue binding forms are skipped.** `{#each x as y}` and
  `v-for="x in xs"` parse as JS but mean something else.
- **Razor has no import edges**, and an attribute-bound handler carries none.
- **Object Pascal's `extends`/`implements` split is a naming heuristic**,
  inferred from the `I`-prefix convention rather than a language guarantee.
- **GDScript resolves no `uid://` path and no string dispatch**, and a script
  without `class_name` gets no class symbol.
- **A Godot `addons/` tree is exempt from dead-code reporting** only when a
  `project.godot` sits above it, so a plugin's own repo reports normally.
- **VB.NET leaves a few constructs unparsed**, XML and tuple literals among them.
- **C has no health dialect.** It shares the C++ grammar for parsing but reaches
  no health walker map, so it gets graph coverage without markers.
- **Scala import resolution is partial**, and ScalaTest's infix DSL
  (`x shouldBe y`) is not counted as an assertion.

Per-language mechanics behind these:
[architecture/language-support.md](../architecture/language-support.md).

---

## Roadmap

| Language | Target | Next |
|----------|--------|------|
| Vue | Full | Options-API `pair`-function members, `v-for` head bindings |
| Svelte | Full | `{#each}` head bindings, object-literal attributes, `.svelte.ts` rune modules |
| Kotlin | Full (health) | Dataflow is **blocked on the grammar** and needs a grammar upgrade or a text-based jump seam |
| Scala | Full (health) | Dataflow dialect, combinator (`.map` / `.foreach`) loop tracking |
| Ruby | Full (health) | Dataflow dialect, LCOM4 via `@ivar` grouping |
| C# | Full (health) | Dataflow dialect |
| Dart | Good | riverpod / get_it dynamic hints, dataflow dialect |
| GDScript | Good | The health dialects (complexity, performance, dataflow) that would take it to Full; the grammar supports all three |
| Object Pascal | Good | Assertion and performance markers, a dedicated `uses` resolver |
| VB.NET | Good | Health markers, project-level `<Import Include=...>` as implicit imports |
| Elixir | Good | Health markers, and a call-resolution strategy beyond same-file |
| F# | Good | Health markers, and a resolver that reads the AST index instead of the declared-name regex |
| Objective-C | Good | Health markers, a resolver that reads the Xcode project rather than file stems, and pairing a header with its implementation across files |
| SQL / dbt | — | Column-level blast radius |
| Shell | — | Shebang detection for extensionless executables |
| HTML | Lightweight | A regex import tier for template dialects, gated on a framework manifest |

---

## See also

- **[GRAPH.md](GRAPH.md)** · the graph itself: edge vocabulary, resolution origins, execution flows
- **[architecture/language-support.md](../architecture/language-support.md)** · pipeline internals, the call-resolution architecture, and the contributor recipe for adding a language
- [CODE_HEALTH.md](CODE_HEALTH.md) · health markers and per-language precision
- [WORKSPACES.md](../scale/WORKSPACES.md) · cross-repo contracts and co-change
