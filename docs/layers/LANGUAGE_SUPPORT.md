# Language Support

**26 languages parsed to a full AST, 40 on the five-rung support ladder, and
framework-aware edges where an ecosystem handler exists.** Every language lands
on one rung, and the rung tells you which parts of the pipeline produce real
output for it. Files in any other language still appear in the wiki and are
tracked through git history. No LLM key is needed for any of this: parsing,
resolution and health markers are all static.

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
  <img src="https://img.shields.io/badge/COBOL-005CA5?style=flat-square" alt="COBOL" />
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
[How accurate is the graph per language](#how-accurate-is-the-graph-per-language) ·
[Full tier](#full-tier) · [Good tier](#good-tier) ·
[Beyond code files](#beyond-code-files) ·
[Code-health coverage](#code-health-coverage) ·
[Known ceilings](#known-ceilings) · [Roadmap](#roadmap) ·
[Requesting or adding a language](#requesting-or-adding-a-language)

---

## Tiers

| Tier | Languages | What you get |
|------|-----------|--------------|
| **Full** (13) | Python · TypeScript · JavaScript · Svelte · Vue · Java · Kotlin · Go · Rust · C++ · C# · Scala · Ruby | The whole pipeline: AST symbols, import resolution, a resolved call graph, heritage, docstrings, framework edges and code-health markers |
| **Good** (11) | C · Swift · PHP · Dart · Object Pascal · COBOL · GDScript · VB.NET · Elixir · F# · Objective-C | Everything above except the full health suite. Dart and Object Pascal get health markers; C, F# and Objective-C get the complexity-derived ones; the rest get none yet |
| **Partial** (2) | Luau / Roblox · Razor / Blazor | Luau: AST symbols and `require()` resolution (Rojo and `.luaurc` aware), no health markers. Razor: a component symbol per file, call edges from `@code` blocks and component tags, C# health markers, no import edges yet |
| **Lightweight** (6) | Clojure · Haskell · Lean 4 · Erlang · HTML · QML | A real file-to-file import graph, no symbol-level claims |
| **Structural** (8) | R · Zig · Julia · Elm · OCaml · Crystal · Nim · D | Git history only: blame, hotspots, co-change. No AST parsing |

Tree-sitter parsing covers the first three rungs: those are the **26 languages
parsed to a full AST**. The bottom two rungs come from git history and import
extraction, and all five together make the **40** on the ladder.

[SQL / dbt](#sql--dbt) and [shell](#shell) sit outside the ladder on purpose,
because neither fits a rung: SQL gets symbols and health markers but no call
graph, and shell gets symbols, `source` edges and complexity but no dead-code
claims. Cross-repo contracts (HTTP routes, gRPC, queues, sockets, database
tables) are listed per language and framework in
[WORKSPACES.md](../scale/WORKSPACES.md#api-contract-extraction).

## What the pipeline gives each tier

| Stage | Full | Good | Partial | Lightweight | Structural |
|-------|:----:|:----:|:-------:|:-----------:|:----------:|
| File discovery and git history | ✅ | ✅ | ✅ | ✅ | ✅ |
| AST symbol extraction | ✅ | ✅ | ✅ | no | no |
| Import resolution | ✅ | ✅ | Luau | file-level | no |
| Call graph edges | ✅ | ✅ | ✅ | no | no |
| Heritage (extends / implements) | ✅ | ✅ | no | no | no |
| Named bindings | ✅ | ✅ | no | no | no |
| Code-health markers | ✅ | partial, see [coverage](#code-health-coverage) | Razor | no | no |
| Dead code detection | ✅ | ✅* | ✅ | ✅ | ✅ |
| Semantic search and wiki pages | ✅ | ✅ | ✅ | ✅ | ✅ |

Scala, Elixir and F# have dedicated import resolvers with known gaps. Object
Pascal and Objective-C resolve through a generic unit-name or header-stem match,
and COBOL resolves literal program calls, not imports. Every other Full and Good
language has a dedicated import resolver.

\* COBOL is excluded from dead-code claims: JCL, schedulers and dynamic program
calls are entry paths the repository graph cannot observe.

Every `calls` edge records how it was resolved and carries a confidence from
that, and a call on a typed variable resolves through the variable's declared
type. [GRAPH.md](GRAPH.md) covers resolution origins, receiver typing and
execution flows.

## How accurate is the graph per language

Call-graph precision and recall differ by language, because resolution depends
on what the grammar and the type system expose. Measured figures per language,
with the corpus and method behind them, are in
[BENCHMARKS.md](../BENCHMARKS.md#accuracy-by-language). This page states only
what each rung covers, not how well.

---

## Full tier

AST parsing, import resolution, call resolution, named bindings, heritage,
docstrings, framework-aware edges and code-health markers.

| Language | Extensions | Import resolution |
|----------|-----------|--------------|
| **Python** | `.py` `.pyi` | Source-root-aware module index (`src/`, monorepo `packages/*/src`, PEP 420), `__init__.py` re-export barrels |
| **TypeScript** | `.ts` `.tsx` `.mts` `.cts` | ESM and `require()`, tsconfig path aliases, npm/yarn/pnpm workspaces, `export * from` barrels |
| **JavaScript** | `.js` `.jsx` `.mjs` `.cjs` | `import` and `require()`, including CommonJS re-export shapes |
| **Svelte** | `.svelte` | The TS/JS resolver plus SvelteKit's `$lib` and `#` subpath imports |
| **Vue** | `.vue` | The TS/JS resolver plus config aliases, directory-index components, router `import()` specifiers |
| **Java** | `.java` | Maven and Gradle reactor discovery, JPMS, `import static`, package fan-out |
| **Kotlin** | `.kt` `.kts` | Shares the JVM index with Java, so resolution is cross-language |
| **Go** | `.go` | Multi-module `go.mod` discovery; a package import fans out to every file in the package |
| **Rust** | `.rs` | `use crate::` / `super::` / `self::` with `Cargo.toml` |
| **C++** | `.cpp` `.cc` `.cxx` `.h` `.hh` `.hpp` `.hxx` `.inl` `.ipp` `.tpp` `.inc` | `#include` via `compile_commands.json` plus CMake and Bazel header maps, header/implementation pairing |
| **C#** | `.cs` | `using`, `global using` and aliases via `.csproj` / `.sln`, MSBuild project graph, `partial` class linking |
| **Scala** | `.scala` | The shared JVM index, with SBT and Mill build parsing as fallback |
| **Ruby** | `.rb` | `require` / `require_relative` with `$LOAD_PATH` probing, Gemfile externals, Rails / Zeitwerk autoloading |

### Framework-aware edges

Routes connect to handlers, DI registrations to implementations, and test
fixtures to the tests that use them.

| Language | Frameworks |
|----------|-----------|
| Python | Django, FastAPI, Flask, Celery, pytest fixtures |
| Ruby | Rails (routes to controller actions, Zeitwerk), RSpec mirror edges |
| Java / Kotlin | Spring, Jakarta / JPA, Quarkus, Micronaut, Android manifest |
| C# | ASP.NET (attribute and minimal API), EF Core, gRPC-dotnet, host-builder extensions, CommunityToolkit MVVM |
| Go | net/http, gin, echo, chi, gRPC server registration |
| Rust | Axum, Actix |
| JS / TS / Svelte | Next.js App Router, Hono / Fastify / Koa / Elysia, Remix / SvelteKit / Astro, tRPC, Express / NestJS, Angular |
| C++ | GoogleTest, Catch2, Boost.Test, doctest, Google Benchmark, libFuzzer |
| PHP | Laravel, TYPO3 |
| Dart | Flutter route tables, `runApp()` and widget trees |
| GDScript | Godot scenes, autoloads, signal connections, `class_name` |

The dead-code analyzer also knows each ecosystem's entry points and generated
files, so framework-invoked code is not reported as unreachable.

### Single-file components and Razor

A `.svelte` or `.vue` file is projected into TypeScript at the same byte
offsets, so components get the TypeScript queries and health markers, and every
line number points at the real source. Razor (`.razor`, `.cshtml`) does the same
with its `@code` blocks projected into C#; it sits on the Partial rung because
it has no import edges yet.

---

## Good tier

AST parsing, symbol extraction and static call resolution, plus the
dependency surface each language's syntax supports.

| Language | Extensions | Dependency resolution |
|----------|-----------|--------------|
| **C** | `.c` | `#include` via `compile_commands.json` (shares the C++ grammar) |
| **Swift** | `.swift` | SPM `Package.swift` targets, intra-module type references, `@main` entry points |
| **PHP** | `.php` | `use` declarations through PSR-4 from every `composer.json`, same-namespace and fully qualified class references |
| **Dart** | `.dart` | `import` / `export` / `part`, `package:` via every `pubspec.yaml` |
| **Object Pascal** | `.pas` `.pp` `.dpr` `.dpk` `.lpr` | `uses` clauses via unit-name to file-stem match; project files as entry points |
| **COBOL** | `.cbl` `.cob` `.cobol` `.cpy` | Literal `CALL` and `PERFORM` targets; dynamic calls and `COPY` stay silent |
| **GDScript** | `.gd` | `preload` / `load` / `extends "res://..."` from the nearest `project.godot` |
| **VB.NET** | `.vb` | `Imports` through the same MSBuild project index C# uses |
| **Elixir** | `.ex` `.exs` | `alias` / `import` / `require` / `use` against a `defmodule` index, Mix path convention as fallback |
| **F#** | `.fs` `.fsx` `.fsi` | `open` against a declared-name index plus the fsproj compile order; `.fsi` files contribute imports only |
| **Objective-C** | `.m` `.mm` `.h` | `#import` / `#include` through the header-stem index |

---

## Beyond code files

### SQL / dbt

SQL is parsed by sqlglot (multi-dialect, error-tolerant), not tree-sitter.

- `CREATE TABLE` / `VIEW` become class-kind symbols with columns in the
  signature; `CREATE FUNCTION` / `PROCEDURE` become functions. Set `sql_dialect`
  in config for dialect-specific syntax. A file that fails to parse is kept as
  plain text.
- dbt `{{ ref('model') }}` and `{{ source(...) }}` become import edges, so model
  lineage, hotspots, co-change and ownership work on dbt projects. Outside dbt,
  `.sql` files get symbols and pages but no import edges.
- In workspace mode, tables defined by DDL, migrations or ORM models pair with
  the SQL in app code that reads them. See [WORKSPACES.md](../scale/WORKSPACES.md).
- Stored routines get cyclomatic complexity, plus `sql_select_star`,
  `sql_update_delete_without_where` and `sql_cartesian_join`. These are
  uncalibrated, so they appear as findings and never move the defect score.

### Shell

`.sh` / `.bash` / `.zsh`: functions become symbols, `source` / `.` statements
become import edges (including `$SCRIPT_DIR/x.sh` and `$(dirname "$0")/x.sh`
idioms), and calls to functions in the same or a sourced file resolve. Shell
gets function-level complexity. No dead-code flagging: scripts are invoked by
name, so static reachability says nothing.

### Lightweight tier

Clojure, Haskell, Lean 4, Erlang, HTML and QML get a file-level import graph
resolved against a declared module-name index, with no symbol-level claims.
HTML contributes `<script src>` and `<link href>` edges (so `index.html` reaches
`src/main.ts` in a Vite or webpack app). HTML and QML files are never flagged
as dead code, since whether a page or a Qt component is reachable is decided at
runtime.

### Config and data

OpenAPI, Protobuf, GraphQL, Dockerfile, Makefile, YAML, JSON, TOML, Terraform
and Markdown appear in the file tree and the wiki. OpenAPI and Protobuf also feed
workspace contracts, and Terraform feeds AWS Lambda handler edges. Godot resource
files (`.tscn`, `.tres`, `.escn`, `project.godot`, `plugin.cfg`) carry no symbols
but are read for the script paths they name.

---

## Code-health coverage

Health markers need a per-language walker map that is separate from parsing, so
a language can parse fully for the graph and still produce no health findings.
This table is why a language is Full and not Good.

| Language | Complexity / nesting | Class metrics | Assertion smells | Mock saturation | Assertion-free test | Extract Method | Performance risk |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Python | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| TypeScript / JavaScript | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Svelte · Vue | ✅ | ✅ | ✅ | no | no | ✅ | ✅ |
| Java | ✅ | ✅ | ✅ | no | no | ✅ | ✅ |
| Go | ✅ | n/a | ✅ | no | no | ✅ | ✅ |
| Rust · C++ | ✅ | ✅ | ✅ | no | no | ✅ | ✅ |
| C# · Scala · Ruby | ✅ | ✅ | ✅ | no | no | no | ✅ |
| Kotlin | ✅ | ✅ | ✅ | no | no | no | ✅ |
| Dart | ✅ | n/a | ✅ | no | no | no | ✅ |
| Object Pascal | ✅ | n/a | ✅ | n/a | n/a | no | ✅ |
| Razor | ✅ | n/a | n/a | n/a | n/a | no | ✅ |
| C · F# · Objective-C | ✅ | no | no | no | no | no | no |
| Shell | ✅ | n/a | n/a | n/a | n/a | n/a | n/a |

✅ supported, "no" not available yet, n/a the language cannot carry that metric.
An unmapped language produces no findings, never guessed ones. The two
test-quality markers (mock saturation, assertion-free test) are advisory and
never deduct from a score. Why Go and Java are held back from them, and why
Kotlin's Extract Method waits on its grammar, is in
[architecture/language-support.md](../architecture/language-support.md#test-quality-markers-per-language).
Per-marker detail is in [CODE_HEALTH.md](CODE_HEALTH.md).

---

## Known ceilings

- **Template dialects are invisible.** Django/Jinja, Go templates, ERB,
  Handlebars, Blade and Thymeleaf parse as HTML and yield nothing.
- **Svelte and Vue binding forms are skipped.** `{#each x as y}` and
  `v-for="x in xs"` are not resolved, and the unused-export pass is off for
  both, since props are set by the parent as markup attributes.
- **Object Pascal type kinds collapse** to `kind="class"`, and its `extends`
  vs `implements` split follows the `I`-prefix naming convention.
- **COBOL is COBOL-85 shaped.** `COPY` does not create an edge; dynamic program
  names and dialect extensions stay silent.
- **Razor has no import edges.**
- **GDScript string dispatch is unresolved**, and a script without `class_name`
  gets no class symbol.
- **VB.NET leaves a few constructs unparsed**, XML and tuple literals among them.
- **C has complexity markers only.** It has no performance or Extract Method
  support.
- **Scala import resolution is partial**, and ScalaTest's infix DSL
  (`x shouldBe y`) is not counted as an assertion.

Mechanics behind these:
[architecture/language-support.md](../architecture/language-support.md#per-language-mechanics).

---

## Roadmap

| Language | Next |
|----------|------|
| Vue · Svelte | Template binding forms (`v-for`, `{#each}`) |
| Kotlin | Extract Method, which needs a grammar that labels control flow |
| Scala · Ruby · C# | Extract Method (dataflow dialect) |
| GDScript | Health markers (complexity, performance, dataflow) |
| Object Pascal | A dedicated `uses` resolver |
| COBOL | Copybook resolution, dialect coverage, health markers |
| VB.NET · Elixir | Health markers |
| F# · Objective-C | Health markers beyond complexity |
| SQL / dbt | Column-level blast radius |
| Shell | Shebang detection for extensionless executables |

---

## Requesting or adding a language

To ask for a language, open an issue on
[GitHub](https://github.com/repowise-dev/repowise/issues) with a public repo
that uses it. To add one yourself, the contributor recipe (a `LanguageSpec`, a
tag, a tree-sitter query, a config entry and the grammar dependency) is in
[architecture/language-support.md](../architecture/language-support.md#adding-a-new-language),
and the general contribution flow is in
[CONTRIBUTING.md](../../.github/CONTRIBUTING.md). A new language starts on the
Good or Partial rung; health markers come from separate registries described in
the same architecture page.

---

## See also

- [GRAPH.md](GRAPH.md) · edge vocabulary, resolution origins, execution flows
- [architecture/language-support.md](../architecture/language-support.md) · pipeline internals and per-language mechanics
- [CODE_HEALTH.md](CODE_HEALTH.md) · health markers
- [BENCHMARKS.md](../BENCHMARKS.md#accuracy-by-language) · measured accuracy per language
- [WORKSPACES.md](../scale/WORKSPACES.md) · cross-repo contracts
