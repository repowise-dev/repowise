# Language Support

**26 languages parsed to a full AST · 40 on the five-rung ladder ·
framework-aware where an ecosystem handler exists.** "Do you support X" has five useful answers
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
| **Good** (11) | C · Swift · PHP · Dart · Object Pascal · COBOL · GDScript · VB.NET · Elixir · F# · Objective-C | Everything above except the full health suite. Dart and Object Pascal *do* get health markers, and C, F# and Objective-C get the complexity-derived ones; Swift, PHP, COBOL, GDScript, VB.NET and Elixir don't yet. GDScript has a dedicated import resolver and Godot-specific framework edges but no named bindings (see [Known gaps](../architecture/language-support.md#gdscript--godot)) |
| **Partial** (2) | Luau / Roblox · Razor / Blazor | Luau: AST symbols and `require()` resolution (Rojo / `.luaurc` aware), no health markers yet. Razor: a component symbol per file, call edges from `@code` blocks and component tags, C# health markers; no import resolution yet |
| | | ⎯⎯ *tree-sitter parsing stops here. The rungs below are derived from git and imports, not from an AST.* ⎯⎯ |
| **Lightweight** (6) | Clojure · Haskell · Lean 4 · Erlang · HTML · QML | A real file-to-file import graph, no symbol-level claims |
| **Structural** (8) | R · Zig · Julia · Elm · OCaml · Crystal · Nim · D | Git history only: blame, hotspots, co-change. No AST parsing |

The first three rungs are the **26 languages parsed to a full AST**; all five are
the **40** on the ladder. Both numbers are worth stating and neither is worth
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
| Dead code detection | ✅ | ✅* | ✅ | ✅ | ✅ |
| Semantic search & wiki pages | ✅ | ✅ | ✅ | ✅ | ✅ |

Scala's import resolution is partial: it shares the JVM index with Java and
Kotlin and falls back to parsing SBT / Mill build files. Every other Full and
Good language resolves imports outright. On the Partial rung the two languages
split: Luau resolves `require()` and has no health markers, Razor has C#
health markers and no import edges yet.

\* COBOL is excluded from dead-code claims: JCL, schedulers and dynamic program
calls are external entry paths that the repository graph cannot observe.

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
| `enclosing_class` | 0.95 | A bare call bound to the caller's own class |
| `receiver_same_file` | 0.93 | The receiver names a class in the calling file |
| `receiver_typed_same_file` | 0.93 | The receiver's declared type is a class in this file |
| `receiver_field_same_file` | 0.93 | The receiver is a field whose type is a class in this file |
| `receiver_framework_same_file` | 0.93 | A framework decorator retyped the receiver to a class in this file |
| `scoped_name` | 0.93 | The call names its class directly, and that class declares the method |
| `receiver_extension_same_file` | 0.93 | An extension method in this file extends the receiver's type |
| `return_type_same_file` | 0.93 | The inner call's declared return type is a class in this file |
| `same_package` | 0.90 | The target is in a sibling file that needs no import |
| `import_scoped` | 0.90 | The name was imported from the file that defines it |
| `receiver_same_package` | 0.90 | The receiver is a class in the same package |
| `receiver_typed_same_package` | 0.90 | The receiver's declared type is a class in the same package |
| `receiver_field_same_package` | 0.90 | The receiver is a field whose type is a class in the same package |
| `receiver_framework_same_package` | 0.90 | A framework decorator retyped the receiver to a class in the same package |
| `return_type_same_package` | 0.90 | The inner call's declared return type is a class in the same package |
| `self_inherited` | 0.90 | `self` / `this` names a method declared by one ancestor |
| `enclosing_inherited` | 0.90 | A bare call names a method declared by one ancestor |
| `package_alias` | 0.88 | The call is qualified by a package the repo declares |
| `module_alias` | 0.88 | The receiver is an imported module |
| `crate_root` | 0.88 | The reference is scoped to its Rust crate |
| `receiver_import` | 0.88 | The receiver's class was found in an imported file |
| `receiver_typed_import` | 0.88 | The receiver's type was read off its declaration, then found in an imported file |
| `receiver_field_import` | 0.88 | The receiver is a field whose type was found in an imported file |
| `receiver_framework_import` | 0.88 | A framework decorator retyped the receiver to a class in an imported file |
| `receiver_extension_import` | 0.88 | An imported file extends the receiver's type with this method |
| `return_type_import` | 0.88 | The inner call's declared return type was found in an imported file |
| `import_merged` | 0.85 | The name is in one of the imported files, but the file is unattributed |
| `same_target` | 0.85 | The target is a sibling translation unit in the same build target |
| `receiver_global` | 0.75 | The `(class, method)` pair exists *somewhere* in the repo |
| `receiver_typed_global` | 0.75 | The inferred type and method pair exists somewhere in the repo |
| `receiver_field_global` | 0.75 | The field's type and method pair exists somewhere in the repo |
| `receiver_framework_global` | 0.75 | The framework type and method pair exists somewhere in the repo |
| `receiver_extension_global` | 0.75 | One extension method in the repo extends this type with this name |
| `return_type_global` | 0.75 | The returned type and method pair exists somewhere in the repo |
| `global_unique` | 0.50 | The name is unique repo-wide. **A guess, and labelled as one** |

That is the complete vocabulary of 37. You can filter a graph by confidence, and both
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

AST parsing, symbol extraction and static call resolution, plus the
language-appropriate dependency surface. Languages with imports add named
bindings, heritage and a workspace resolver where their syntax supports them.

| Language | Extensions | Dependency resolution |
|----------|-----------|--------------|
| **C** | `.c` | `#include` via `compile_commands.json` (shares the C++ grammar) |
| **Swift** | `.swift` | SPM `Package.swift` target → directory mapping, intra-module type references, `@main` entry points |
| **PHP** | `.php` | `use Foo\Bar\Baz` with composer.json PSR-4 longest-prefix resolution; Laravel, TYPO3 edges |
| **Dart** | `.dart` | `import` / `export` / `part` URIs, `package:` via every `pubspec.yaml`, Flutter route tables and `runApp()` edges. **Health markers included** |
| **COBOL** | `.cbl` `.cob` `.cobol` `.cpy` | Program IDs, sections, paragraphs and data levels; literal `CALL` and `PERFORM` targets resolve to program/procedure symbols. Dynamic calls and `COPY` edges are deliberately silent |

COBOL is Good rather than Full because it has no code-health walker. Its static
dependency surface is narrower than a module language's: literal program calls
and local procedure transfers resolve, while copybook resolution, dynamic
`CALL data-item`, dialect-specific syntax and source-format preprocessing are
explicitly deferred. Dead-code findings are also suppressed because JCL and
scheduler entry points usually live outside the indexed source graph.
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

| Language | Complexity / nesting | Class metrics | Assertion smells | Mock saturation | Assertion-free test | Extract Method | Performance risk |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Python | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| TypeScript / JavaScript | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Svelte · Vue | ✅ | ✅ | ✅ | later | later | ✅ | ✅ |
| Java | ✅ | ✅ | ✅ | blocked | blocked | ✅ | ✅ |
| Go | ✅ | n/a | ✅ | blocked | blocked | ✅ | ✅ |
| Rust | ✅ | ✅ | ✅ | later | later | ✅ | ✅ |
| C++ | ✅ | ✅ | ✅ | later | later | ✅ | ✅ |
| C# | ✅ | ✅ | ✅ | later | later | later | ✅ |
| Kotlin | ✅ | ✅ | ✅ | later | later | blocked | ✅ |
| Scala | ✅ | ✅ | ✅ | later | later | later | ✅ |
| Ruby | ✅ | ✅ | ✅ | later | later | later | ✅ |
| Dart | ✅ | n/a | ✅ | later | later | later | ✅ |
| Object Pascal | ✅ | n/a | later | n/a | n/a | later | n/a |
| Razor | ✅ | n/a | n/a | n/a | n/a | later | ✅ |
| Shell | ✅ | n/a | n/a | n/a | n/a | n/a | n/a |

Every cell is a deliberate call, not an oversight: a language reaches a dialect
or it stays silent, and an `n/a` records a metric the language cannot carry
rather than one nobody got to. Kotlin's Extract Method is **blocked on the
grammar**, not unscheduled.

**The assertion count** is the denominator of every test-quality ratio, counted
in two tiers (`analysis/health/asserts/lexicon.py`). The narrow tier is the
`assert` / `expect` prefix match that `large_assertion_block` and
`duplicated_assertion_block` are calibrated on, and it never changes. The broad
tier adds a per-language row plus anything a repository declares in the
`assertions:` block, and feeds the advisory count alone, so a vocabulary edit
cannot move a score. Broad-tier names are matched exactly, never as prefixes:
over three corpora the `check` / `validate` / `approve` / `fail` prefix families
matched ~600 production functions under test and not one assertion.

Counted assertions, narrow tier then both, when the tiers were introduced:

| Corpus | Test files | Narrow | Both |
|---|---|---|---|
| Go, stdlib `t.Errorf` idiom | 502 | 87 | **3,454** |
| Go, testify idiom | 476 | 1,586 | **3,656** |
| TypeScript, should.js present | 521 | 9,956 | **11,165** |
| TypeScript, no should.js | 574 | 15,595 | 15,595 |

Go was effectively invisible. Assertion *runs* were byte-identical in every
corpus, which is what makes the broad tier safe.

**Mock saturation** measures a test's mock setup against its assertions, so it
needs both a mock vocabulary and a trustworthy assertion count. The vocabulary
is one data row per language in
`analysis/health/mocks/lexicon.py`; a language with no row produces no signal at
all.

Because the marker is advisory it ships below the 70% bar, so the line is not
drawn on the precision number alone: **a language ships when its false positives
are the families the marker already declares, and is blocked when its failure is
a different one the marker cannot name.** TypeScript's every false positive was
the known assertion-observes-production-output family. The two blocked languages
fail differently, and for the same underlying reason — the assertion count is the
denominator of every ratio, so a language whose assertions cannot be counted
honestly cannot carry the marker at all:

- **Go** — the assertion half is fixed (table above). What still blocks the
  marker is the other half: no row in `mocks/lexicon.py` and no hand-labelled
  precision figure.
- **Java** — Mockito states a test's real checks as `verify(...)`, which is
  deliberately not an assertion here (counting it would hide the very tests this
  marker looks for). An over-mocked Java test therefore arrives with one vacuous
  `assertDoesNotThrow` as its whole denominator, so the marker misses the tests
  it is for and the ratio peaks instead on large controller tests whose setup is
  irreducible fixture. Measured at 20% precision over 30 hand-labelled findings,
  flat across every threshold tried — but the mechanism above, not that number,
  is why it is blocked. Java now **does** carry a verification vocabulary, added
  for `assertion_free_test`, and it deliberately does not help here: Mockito
  verification is 1,087 calls over those 299 test files, and it reaches
  `verification_count` rather than `assertion_count` precisely so that this
  marker keeps dividing by state assertions alone.

**Assertion-free test** asks whether a test case checks anything at all, so it
needs two things the other markers do not: a per-function "is this a test case"
rule, and a way to tell that a test checks nothing. The second is deliberately not one
count: `assertion_count` is calibrated and shared with a scored marker, so the
shapes it cannot classify — a hand-rolled `throw`, an assertion bound to a name
— are answered beside it rather than folded into it. The rules live in
`analysis/health/complexity/test_case.py`, one data row per language — a name
prefix for Python, `go test`'s own case-sensitive `TestXxx` rule for Go, the
`it(...)` / `test(...)` callback for JS/TS, and `@Test` and its JUnit siblings
for Java. A language with no row classifies nothing. Go and Java are classified
and counted, which is how their precision below was measured, but the marker
does not report on them — the shipping set is a constant in the detector.

Measured precision, hand-labelled: **71%** on TypeScript (31 findings, the
complete population of two corpora) and **86%** on Python (29 findings, a
systematic sample of 172). **Both are now a floor rather than a current
reading**, as `mock_saturated_test`'s are a ceiling: three false-positive
families have been closed since they were labelled, which removed findings
from the numerator's denominator without re-labelling what survives. On the
TypeScript population the same 22 true findings now sit in 26 rather than 31,
which is 84.6% if none of the five removals took a true finding with it — they
were each read, and none did. The marker ships advisory on both: the bar below is a
property of the marker rather than of one language, and neither reading settles
it on a population this size.

Twenty-nine items cannot settle a 70% bar: 86% on that sample carries a 95%
interval of roughly 68% to 96%, whose lower bound sits below the bar it appears
to clear. The reading is consistent with clearing it and does not demonstrate
it. What moved Python is that a test's oracle now reaches it from a function it
calls in the same file, described below. That barely moved TypeScript, because a
JS/TS suite keeps its helpers in a separate module; resolving the same
delegation *across* a file is what took TypeScript from 43% to 71%, and how it
is done is in the section after next.

An earlier pass published 53% for Python. Re-labelling the pre-change findings
of the same corpus, drawn the same way and read against this pass's rubric,
gives 68.8% (22 of 32), so the gain below is that 68.8 to 86.2 and the 53% is
superseded rather than contradicted. The two passes are not reconciled: 15
points of the difference is labelling, on samples of about thirty, and the
rubric behind the earlier number was not recorded. TypeScript read 52% then and 51% on
the pass after it; re-labelled here it reads 43.1%, so the drift is on both
languages and in the same direction rather than on Python alone. That withdraws
the earlier reading of it as sample noise: what the three passes differ on is
the rubric, and the only defence against that is to state the rubric and
re-label both arms of a comparison together, which is what the section below
does.

### Resolving a test's oracle across a file boundary

A test that hands its checks to a helper is not assertion-free, and the
assertion count is per function, so the helper's checks are invisible from the
test. Resolving that within one file took Python from 68.8% to 86.2%. A JS/TS
suite keeps its helpers in another module, so the same question had to be asked
of the call graph.

It is asked in two lanes, because **a JS/TS test case is not a graph node**:
every one of them is an anonymous callback handed to `it(...)`, and on one
corpus 0 of 7,354 resolved to a symbol, against 5,212 of 5,212 for Python's
named `def test_x`. Resolving from the enclosing module node instead would let
one delegating test speak for every other test in its file, so it is not done.

- **call-edge** — the test is a symbol; walk its own outgoing call edges, depth
  bounded. Its job on Python is as much to answer *no* authoritatively as to
  answer yes: while it answers, the looser lane below is never consulted.
- **file-edge** — the test is a callback; require both that the *file* has a
  resolved call edge to the asserting symbol and that the *function body* calls
  it by name. The edge alone is file-scoped and the name alone is repo-scoped;
  together they are neither.

Only edges that bind **one** definition are read, which is a harder filter than
the execution index applies. `import_merged` carries 0.85 confidence and means
only "a function of this name exists in one of the files this file imports"; it
was measured resolving `Silent().check()` to `Asserting.check` because the
receiver could not be typed. An extra edge costs a performance pass a cheap
false positive and costs this marker a hidden true finding, so the two filter
differently on purpose.

A suppression is available only where an edge is. An unresolved call is
indistinguishable from a call to something that checks nothing, so it suppresses
nothing and the marker fires — a missing edge leaves a false positive, where a
wrong one would hide a real finding.

Three consequences of that, all leaving findings in place rather than removing
them, and all properties of a repository's layout as much as of the mechanism. A
helper outside a test-named directory is not an oracle, so a suite keeping its
helpers in `src/test-utils/` gets nothing from this while one using
`src/testUtils.ts` does. A re-export is followed one hop, so a single barrel
file works and a barrel of barrels does not. And an oracle living in a
`beforeEach` is an anonymous callback with no symbol, so it is never in the sink
set at all.

Measured on the complete population of two corpora, both arms labelled in one
pass against one rubric: **51 findings at 43.1% to 31 at 71.0%, with none of the
22 true findings lost.** All 20 suppressions trace to three helpers, each read in
full, and each does check something. The sharpest evidence that the oracle is
per function rather than per file: `inspectTreeStructure` and
`testParseSourceCodeDefinitions` live in the same helper module, and only the
second asserts — the first's eleven callers all survive.

An earlier pass published 51% for TypeScript. Re-labelling that same pre-change
population against this pass's rubric gives 43.1%, so the gain is 43.1 to 71.0
and the 51% is superseded rather than contradicted; the difference is that this
pass counts a deliberate, commented "should not throw" as a false positive.

Read that gain with its provenance. Both arms were labelled by one person in one
unblinded pass, by the author of the change, and the re-label moved the baseline
**down** -- the one direction that widens the reported gain. The post figure is
the more defensible of the two, being a complete population rather than a
sample; the 28-point delta is the part carrying the labeller. A second reader on
the same population is the cheapest thing that would settle it. Note also that
22 of 31 is a 95% interval of roughly 53% to 84%, whose lower bound sits below
the 70% bar exactly as Python's 29-item reading does, so neither language
demonstrates the bar on these populations. Four families accounted for the remainder, and three have since been closed
by reading the test rather than the call graph — a wait helper that fails by
throwing, a guard test whose body is a bare `throw`, and an `expect(...)` the
statement scan declines because it is bound to a name or sits inside a nested
function. What remains is a documented no-throw, which is a labelling question
rather than a code one: every test that executes code already fails if that
code raises, so a comment saying "should not throw" names an oracle the marker
cannot distinguish from its absence. None of the four was introduced by
resolving a call.

Python moves on one corpus and not the other, and the split is the point. One
corpus suppresses nothing: its tests keep their oracles inline or in the same
file, where the existing rule already reaches them. The other goes from **501
findings to 346**, and all 155 suppressions trace to just five helper methods --
one of which, a `check_html` on a shared `SimpleTestCase` subclass, accounts for
144 by itself. Every one of the five was read in full and every one asserts, so
the verification is complete by oracle rather than sampled by finding.

That is the base-class idiom listed below as Java's blocker, showing up in
Python: the helper is inherited, so it is neither in the test's file nor named
anything the assertion lexicon recognises. It is also why the depth is **2**
rather than 1: depth 1 resolves only the single largest helper, missing eleven
of the other four's callers, and depth 3 adds four more that reach
`SimpleTestCase._assert_raises_or_warns_cm`, the framework's own `assertRaises`
machinery, which is further than a test's oracle should have to be chased.

A **mock verification counts as an assertion for this marker and not for the one
above**, which is the deliberate inversion described in CODE_HEALTH.md. It
matters only for Java: Python's `assert_called_once`, jest's
`expect(m).toHaveBeenCalled()` and Go's `t.Error` already reach the narrow tier,
so Mockito is the one dialect where verification is invisible. On a 299-test-file
Java corpus it is 1,087 verifications, and counting them takes the marker from
146 findings to 28 — 118 tests that each had a real oracle — while introducing
none.

Counting a test's oracle turned out to be where the work was. Four forms reached
no tier at all before this. Each was found by reading findings that named an
oracle the counter had not seen, and each is answered at the broad tier so that
no calibrated run moves:

| Form | Example | Why it was missed |
|---|---|---|
| Context-manager oracle | `with pytest.raises(ValueError):` | The call is in the `with` header, not a statement |
| Split call shape | `Helpers.assertRejected(...)` | Java states a call as `object` + `name` and exposes no callee node |
| Property-terminated chain | `expect(x).to.be.null` | chai ends in a property, so the statement is not a call |
| Expression-bodied lambda | `it("x", () => expect(a).toBe(b))` | The body is the expression, so there is no statement to classify |

A private helper (`_assert_no_leak(msg)`) was a fifth: the narrow prefixes anchor
at the start of a name and a leading underscore breaks the anchor.

Firing rate fell as they landed — on a 794-test-file Python corpus from 13.0% of
its test cases to 3.7%, and on a 521-test-file TypeScript corpus from 1.6% to
0.4%. A rate is not a precision measurement; what these five changes are measured
to have done is stop the counter missing an oracle that was written down.

**Go and Java are blocked, and for the same structural reason rather than a
threshold.** Both languages conventionally delegate the oracle to a helper, and
the assertion count is per function, so the helper's assertions are invisible
from the test that calls it:

- **Go** — the idiom is to hand `*testing.T` to a helper that asserts, as in
  `parsertest.RunFileCases(t, ...)`. Measured on the findings themselves: **81 of
  87** on one corpus and **119 of 121** on another pass the test handle to a
  call. That is not a false-positive family a marker can declare and live with,
  it is nearly the whole population, and suppressing on it would leave the marker
  firing a handful of times per repository.
- **Java** — the same shape without the handle, a helper on the test class that
  asserts on the test's behalf. Hand-labelled at **33%** over 18 findings, and 10
  of its 12 false positives were this family.

Separating them needs the assertions of a called function to reach its caller.
Within a single file that now happens. Neither language is unblocked by it: Go
hands `*testing.T` to a package-level helper and Java inherits a base-class
method, and both live in another file, so re-admitting either is a measurement
of its own.

**A called function's assertions reach its caller, inside one file.** The
assertion count is per function, so a test that hands its checks to a helper
beside it read as checking nothing. That was the largest false-positive family
in every language the marker reports on. The walk now records the names each
function calls, and the marker resolves them against same-file functions that
assert. It counts nothing and no calibrated marker reads it.

What it suppresses, by corpus:

| Corpus | Findings | Test cases | Rate |
|---|---|---|---|
| 2,012-file Python | 763 to 501 | 17,966 | 4.2% to 2.8% |
| 794-file Python | 194 to 172 | 5,212 | 3.7% to 3.3% |
| 374-file TypeScript | 35 to 33 | 7,354 | 0.5% to 0.4% |
| 562-file TypeScript | 18 to 18 | 4,761 | 0.4% to 0.4% |

Six suppressions were read by hand across the two Python corpora and each is
the declared family: a test whose whole body delegates to a helper that asserts,
or one that parametrises a sibling test by calling it. The resolution matches
names against same-file names and drops the receiver, so a call to an imported
function sharing a local helper's name, or a method on a test-local double
sharing a module-level function's name, suppresses wrongly. Both hide a finding
rather than invent one. No instance turned up in the corpora above, and the
shape is pinned by a test.

What it deliberately does not reach is anything needing the call graph, which
this pass does not consult: `super().test_x(...)`, a package-level Go helper
handed the test handle, and the shared JS/TS fixture module that is most of
TypeScript's remaining error.

**A `.tsx` file is read with the JSX grammar.** It arrives tagged `typescript`,
and the grammar that tag selects fails on the first `<Component />`; everything
after it lands in ERROR recovery. The health pass picks the grammar from the
path instead, in all three places it parses: the complexity walk, the clone
tokenizer and the dataflow CFG. A React suite is read the way a `.ts` suite
always was. What it was costing, measured on two React corpora (374 and 562
`.tsx` files):

| | 374-file corpus | 562-file corpus |
|---|---|---|
| Assertions counted on `.tsx` | 1,367 to 2,085 | 175 to 377 |
| Assertion runs on `.tsx` | 264 to 391 | 30 to 69 |
| `.tsx` files whose clone token stream changed | 365 of 374 | 550 of 562 |
| Function names over 60 chars, a proxy for recovery wreckage | 124 to 0 | 20 to 3 |
| `assertion_free_test` on `.tsx` | 15 to 0 | 0 to 1 |
| Non-`.tsx` files whose walk or token stream moved | 0 of 1,165 | 0 of 1,868 |

Every one of those 15 findings was the grammar rather than the test, which is
why the marker declined `.tsx` files until now. The second corpus's one new
finding is a test that genuinely asserts nothing, hidden by that same rule.

Two calibrated markers read the same walk, and at the time both read it
through the name-keyed `function_metrics`, which kept one row per distinct
function name and so dropped all but one of a file's anonymous `it` callbacks.
The section below removes that key; what follows here is the state as this
grammar fix left it. That key is the
thing to understand here. Recovery had been swallowing whole source spans into
each callback's *name*, and those accidentally unique names were defeating it,
so a `.tsx` file kept rows that a `.ts` file has always lost. Counting names
longer than 60 characters as a proxy for that wreckage: 124 of them on the first
corpus, 0 after. Measured as the share of walked functions that survive the key,
`.tsx` goes from 44.4% to 22.9%, against a `.ts`/`.js` control of 16.0%.

`large_assertion_block` does not move on either corpus (0 and 0, then 1 and 1).
Its floor is fifteen assertions in one run and React cases are short, but the
same name key dropped most of the newly visible runs before that floor was
ever consulted, so the floor was not the whole reason. Removing the key turns
that marker's 0 into a 6 on the second corpus below.

`duplicated_assertion_block` falls from 304 to 262 on the first corpus, and this
is a loss rather than a correction. Six of the lost findings were read by hand
and every one is a genuine run of consecutive assertions, correctly bounded; the
rows were real and the clone partner was real. What was not real is the
mechanism that kept them visible, which was a mangled name defeating a lossy
key. So at this point `.tsx` files under-reported duplicated assertion blocks
exactly as `.ts` files always had, which the next section then fixes for both. The corpus carries more real assertion runs after this
change, 264 to 391, and the marker reports fewer of them. Re-keying
`function_metrics` is the fix for that, and the next section is that change.

Svelte and Vue stay `later` rather than following TypeScript: an SFC is walked as
a TypeScript buffer, but a single-file component is essentially never a test
file, so the row would be untestable rather than useful.

The marker is advisory and never deducts. See
[CODE_HEALTH.md](CODE_HEALTH.md).

### A marker reads the whole walked file, not one row per name

Eleven markers used to read a `function_metrics` map keyed by function name. A
map keyed that way keeps one row per distinct name, and a function name is not
unique inside a file. Every `it(...)` callback in a spec file walks under the name
`it callback`; `__init__` walks under one name on each class in a module. Every
row after the first was dropped before a marker ever saw it. They read
`all_functions` now, the whole walked list, which is what the two advisory
test-quality markers already read for this reason.

How much a file loses to that key is a property of how a language names its
functions rather than of any marker. Measured across the walked functions of
four corpora:

| Corpus | Language | Walked functions | Survive the name key |
|---|---|---|---|
| 1,336 files | TypeScript / TSX | 12,257 | 37.8% |
| 2,194 files | TypeScript / TSX | 17,261 | 72.3% |
| 1,949 files | Python | 12,926 | 91.4% |
| 1,714 files | Python | 30,747 | 87.9% |

These count every walked file, so they do not line up with the test-file-only
`.tsx` figures above and are not meant to.

A JS/TS spec suite is the worst case by a wide margin, because its test bodies
are anonymous callbacks that all walk under one name. Python loses about a tenth
of its rows, mostly same-named methods on neighbouring classes.

What each scoring marker sees, before the change and after. Note that the two
assertion-block markers score but were never fitted on a defect corpus, and they
are the two that move furthest:

| Marker | TS/TSX (1,336 files) | TS/TSX (2,194 files) | Python (1,949 files) | Python (1,714 files) |
|---|---|---|---|---|
| `complex_method` | 496 to 506 | 1,005 to 1,015 | 443 to 468 | 483 to 537 |
| `large_method` | 347 to 382 | 608 to 649 | 192 to 203 | 157 to 168 |
| `nested_complexity` | 232 to 233 | 338 to 341 | 236 to 253 | 286 to 317 |
| `bumpy_road` | 80 to 80 | 161 to 162 | 65 to 71 | 99 to 108 |
| `complex_conditional` | 92 to 96 | 163 to 168 | 67 to 69 | 91 to 101 |
| `primitive_obsession` | 54 to 54 | 106 to 107 | 685 to 859 | 449 to 599 |
| `large_assertion_block` | 0 to 0 | 0 to 6 | 0 to 0 | 11 to 11 |
| `duplicated_assertion_block` | 262 to 2,831 | 175 to 1,711 | 1,098 to 1,108 | 2,360 to 2,477 |

Three markers are missing from that table because they need inputs this
measurement does not build: `brain_method` needs graph centrality, and
`code_age_volatility` and `function_hotspot` need git blame. They read the same
list as the other eight, which is visible in the diff, but how far they move was
not observed.

Most of those cells are single-digit percentages. `complex_method`,
`large_method`, `nested_complexity` and `complex_conditional` each clear ten
percent on one corpus and stay below it on the other three. Two markers move
much further, and they are the two worth reading carefully.

`primitive_obsession` gains a quarter to a third of its findings on both Python
corpora, 685 to 859 and 449 to 599, and almost nothing on either JS/TS one.
Python modules define the same method name on several classes each, and the
row that survived the key was the first of them in the file, so a wide signature
on any of the others was invisible.

`duplicated_assertion_block` is the large one: it rises about tenfold on both
JS/TS corpora and barely moves on either Python one. That is the same fact as
the survival table, seen from the other end.

Six of the gained rows were read in the source, and each of those six is a
correctly bounded run of consecutive assertions. Six labels cannot speak for
2,569 rows, so that is evidence the marker is bounding blocks correctly, not a
precision figure. What does speak for the population is its shape. On the first
corpus the two arms' assertion-run-length histograms have closely matching
profiles while the new arm is about eleven times larger, which is what uniform
thinning looks like and not what a filter that selected for anything would look
like. On that same corpus the marker goes from a median of one finding per test
file to five, with one file at sixty. Neither measurement says whether a gained
row is worth showing a reader; only counts and block lengths were measured.

No floor was added to hold that number down. The name key was an accidental
limiter and replacing it with a deliberate one is a calibration change, which
needs its own defect-corpus evidence rather than a number that looks
comfortable. What bounds the damage today is the `test_quality` category cap of
0.5, which a single medium finding already exceeds: a test file with sixty of
these loses the same half point as a test file with one, so the scores move far
less than the counts do. Whether the finding list itself wants a cap is a
question for the marker, not for the container it reads.

The six findings `large_assertion_block` gains on the second JS/TS corpus were
also read by hand. All six are runs of fifteen or more consecutive assertions in
a single test, which is exactly what that marker declares it looks for, and all
six are anonymous callbacks, so each sat in a file where another function of the
same name had been holding the only row.

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
  `v-for="x in xs"` *parse* as JS but mean something else, so they are skipped: a
  parse that succeeds with the wrong meaning is worse than a skip. Component
  props are set by the parent as markup attributes and never imported by name, so
  the unused-export pass is suppressed for both.
- **Object Pascal type kinds collapse.** `record` / `interface` / class-helper /
  enum / type alias all report as `kind="class"`, and its `extends` vs
  `implements` split is inferred from the `I`-prefix naming convention rather
  than guaranteed by the language.
- **COBOL support is intentionally COBOL-85 shaped.** Literal `CALL` and
  `PERFORM` resolve, and `.cpy` files are parsed when indexed directly, but
  `COPY` does not create an edge. Dynamic program names, dialect extensions and
  fixed/free-format preprocessing beyond what the grammar accepts stay silent.
- **Razor has no import edges**, and an attribute-bound handler carries none.
- **Object Pascal's `extends`/`implements` split is a naming heuristic**,
  inferred from the `I`-prefix convention rather than a language guarantee.
- **A GDScript `uid://` resolves through the `.uid` sidecar Godot writes for
  scripts**, so a `preload` naming one reaches its file. A uid naming a scene
  does not: Godot writes no sidecar for `.tscn` / `.tres`, and the
  `[ext_resource]` header carrying such a uid also carries a `path=`, which is
  what resolves. String dispatch is still unresolved, and a script
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
| COBOL | Good | Copybook resolution, source-format normalization, dialect coverage, health markers |
| VB.NET | Good | Health markers, project-level `<Import Include=...>` as implicit imports |
| Elixir | Good | Health markers, and a call-resolution strategy beyond same-file |
| F# | Good | The health markers beyond complexity, and a resolver that reads the AST index instead of the declared-name regex |
| Objective-C | Good | The health markers beyond complexity, a resolver that reads the Xcode project rather than file stems, and pairing a header with its implementation across files |
| SQL / dbt | — | Column-level blast radius |
| Shell | — | Shebang detection for extensionless executables |
| HTML | Lightweight | A regex import tier for template dialects, gated on a framework manifest |

---

## See also

- **[GRAPH.md](GRAPH.md)** · the graph itself: edge vocabulary, resolution origins, execution flows
- **[architecture/language-support.md](../architecture/language-support.md)** · pipeline internals, the call-resolution architecture, and the contributor recipe for adding a language
- [CODE_HEALTH.md](CODE_HEALTH.md) · health markers and per-language precision
- [WORKSPACES.md](../scale/WORKSPACES.md) · cross-repo contracts and co-change
