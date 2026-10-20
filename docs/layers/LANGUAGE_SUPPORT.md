# Language Support

**27 languages parsed to a full AST, 41 on the five-rung support ladder, and
framework-aware edges where an ecosystem handler exists.** Every language lands
on one rung, and the rung tells you which parts of the pipeline produce real
output for it. Files in any other language still appear in the wiki and are
tracked through git history. No LLM key is needed for any of this: parsing,
resolution and health markers are all static.

<p>
  <strong>Full tier &nbsp;</strong>
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" /> <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript" /> <img src="https://img.shields.io/badge/JavaScript-F7DF1E?style=flat-square&logo=javascript&logoColor=black" alt="JavaScript" /> <img src="https://img.shields.io/badge/Svelte-FF3E00?style=flat-square&logo=svelte&logoColor=white" alt="Svelte" />
  <img src="https://img.shields.io/badge/Vue-42B883?style=flat-square&logo=vuedotjs&logoColor=white" alt="Vue" /> <img src="https://img.shields.io/badge/Astro-BC52EE?style=flat-square&logo=astro&logoColor=white" alt="Astro" /> <img src="https://img.shields.io/badge/Java-ED8B00?style=flat-square&logo=openjdk&logoColor=white" alt="Java" /> <img src="https://img.shields.io/badge/Kotlin-7F52FF?style=flat-square&logo=kotlin&logoColor=white" alt="Kotlin" /> <img src="https://img.shields.io/badge/Go-00ADD8?style=flat-square&logo=go&logoColor=white" alt="Go" />
  <img src="https://img.shields.io/badge/Rust-000000?style=flat-square&logo=rust&logoColor=white" alt="Rust" /> <img src="https://img.shields.io/badge/C++-00599C?style=flat-square&logo=cplusplus&logoColor=white" alt="C++" /> <img src="https://img.shields.io/badge/C%23-512BD4?style=flat-square&logo=csharp&logoColor=white" alt="C#" /> <img src="https://img.shields.io/badge/Scala-DC322F?style=flat-square&logo=scala&logoColor=white" alt="Scala" />
  <img src="https://img.shields.io/badge/Ruby-CC342D?style=flat-square&logo=ruby&logoColor=white" alt="Ruby" />
</p>
<p>
  <strong>Good tier &nbsp;</strong>
  <img src="https://img.shields.io/badge/C-A8B9CC?style=flat-square&logo=c&logoColor=black" alt="C" /> <img src="https://img.shields.io/badge/Swift-F05138?style=flat-square&logo=swift&logoColor=white" alt="Swift" /> <img src="https://img.shields.io/badge/PHP-777BB4?style=flat-square&logo=php&logoColor=white" alt="PHP" /> <img src="https://img.shields.io/badge/Dart-0175C2?style=flat-square&logo=dart&logoColor=white" alt="Dart" />
  <img src="https://img.shields.io/badge/Delphi-EE1F35?style=flat-square&logo=delphi&logoColor=white" alt="Object Pascal / Delphi" /> <img src="https://img.shields.io/badge/COBOL-005CA5?style=flat-square" alt="COBOL" /> <img src="https://img.shields.io/badge/GDScript-478CBF?style=flat-square&logo=godotengine&logoColor=white" alt="GDScript / Godot" /> <img src="https://img.shields.io/badge/VB.NET-945DB7?style=flat-square&logo=dotnet&logoColor=white" alt="VB.NET" />
  <img src="https://img.shields.io/badge/Elixir-6E4A7E?style=flat-square&logo=elixir&logoColor=white" alt="Elixir" /> <img src="https://img.shields.io/badge/F%23-378BBA?style=flat-square&logo=fsharp&logoColor=white" alt="F#" /> <img src="https://img.shields.io/badge/Objective--C-438EFF?style=flat-square&logo=apple&logoColor=white" alt="Objective-C" />
</p>
<p>
  <strong>Partial tier &nbsp;</strong>
  <img src="https://img.shields.io/badge/Luau-00A2FF?style=flat-square&logo=lua&logoColor=white" alt="Luau" /> <img src="https://img.shields.io/badge/Razor-512BD4?style=flat-square&logo=blazor&logoColor=white" alt="Razor / Blazor" />
</p>

**Contents:** [Tiers](#tiers), [What the pipeline gives each tier](#what-the-pipeline-gives-each-tier),
[How accurate is the graph per language](#how-accurate-is-the-graph-per-language),
[Extensions and imports](#extensions-and-imports), [Framework-aware edges](#framework-aware-edges),
[Beyond code files](#beyond-code-files), [Code-health coverage](#code-health-coverage),
[Limits](#limits), [Roadmap](#roadmap), [Requesting or adding a language](#requesting-or-adding-a-language)

## Tiers

| Tier | Languages | What you get |
|------|-----------|--------------|
| **Full** (14) | Python, TypeScript, JavaScript, Svelte, Vue, Astro, Java, Kotlin, Go, Rust, C++, C#, Scala, Ruby | The whole pipeline: AST symbols, import resolution, a resolved call graph, heritage, docstrings, framework edges and code-health markers |
| **Good** (11) | C, Swift, PHP, Dart, Object Pascal, COBOL, GDScript, VB.NET, Elixir, F#, Objective-C | Everything above except the full health suite. Dart, Object Pascal and PHP get health markers; C, Swift, F# and Objective-C get the complexity-derived ones; the rest get none yet |
| **Partial** (2) | Luau / Roblox, Razor / Blazor | Luau: AST symbols and `require()` resolution (Rojo and `.luaurc` aware), no health markers. Razor: a component symbol per file, call edges from `@code` blocks and component tags, C# health markers, no import edges yet |
| **Lightweight** (6) | Clojure, Haskell, Lean 4, Erlang, HTML, QML | A real file-to-file import graph, no symbol-level claims |
| **Structural** (8) | R, Zig, Julia, Elm, OCaml, Crystal, Nim, D | Git history only: blame, hotspots, co-change. No AST parsing |

Tree-sitter parsing covers the first three rungs: those are the **27 languages
parsed to a full AST**. The bottom two rungs come from git history and import
extraction, and all five together make the **41** on the ladder.

[SQL / dbt](#sql--dbt) and [shell](#shell) sit outside the ladder, because
neither fits a rung: SQL gets symbols and health markers but no call graph, and
shell gets symbols, `source` edges and complexity but no dead-code claims.
Cross-repo contracts (HTTP routes, gRPC, queues, sockets, database tables) are
listed per language and framework in
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

Every Full and Good language has a dedicated import resolver except Object
Pascal and Objective-C, which resolve through a generic unit-name or header-stem
match, and COBOL, which resolves literal program calls instead of imports.
Scala, Elixir and F# have dedicated import resolvers with known gaps; see
[Limits](#limits).

\* COBOL is excluded from dead-code claims: JCL, schedulers and dynamic program
calls are entry paths the repository graph cannot observe.

## How accurate is the graph per language

Precision and recall differ by language, because resolution depends on what the
grammar and the type system expose. Measured figures per language, with corpus
and method, are in [BENCHMARKS.md](../BENCHMARKS.md#accuracy-by-language).

### Every call edge says how it was resolved, and how much to trust it

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
| `receiver_chain_same_file` | 0.93 | Every hop of a chained field receiver resolves within this file |
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
| `receiver_chain_import` | 0.88 | A chained field receiver resolves through an imported type at its weakest hop |
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

That is the complete vocabulary of 39. You can filter a graph by confidence, and
both the MCP tools and the web UI surface which origin produced an edge, so an
agent reading an execution flow can tell a fact from an inference. Receiver
typing, execution-flow terminations and the edge types are in
[GRAPH.md](GRAPH.md).

## Extensions and imports

Each Full and Good language claims its usual file extensions (C++ also claims
headers such as `.hpp`, `.inl` and `.tpp`) and resolves imports through its own
build files: tsconfig path aliases and workspaces, Maven and Gradle reactors,
`go.mod`, `Cargo.toml`, `compile_commands.json`, `.csproj` / `.sln`, PSR-4,
`pubspec.yaml`, `project.godot` and so on. The table per language is in
[architecture/language-support.md](../architecture/language-support.md#extensions-and-import-resolution).

`.svelte`, `.vue`, `.astro` and Razor files are projected into TypeScript or C# at the
same byte offsets, so they get that language's queries and health markers and
every line number points at the real source. How the projection works:
[architecture/language-support.md](../architecture/language-support.md#multi-language-files-the-sfc-pattern).

## Framework-aware edges

Routes connect to handlers, DI registrations to implementations, and test
fixtures to the tests that use them. The dead-code analyzer also knows each
ecosystem's entry points and generated files, so framework-invoked code is not
reported as unreachable.

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

## Beyond code files

### SQL / dbt

- Parsed by sqlglot (multi-dialect, error-tolerant), not tree-sitter. Set
  `sql_dialect` in config for dialect-specific syntax. A file that fails to
  parse is kept as plain text.
- `CREATE TABLE` / `VIEW` become class-kind symbols with columns in the
  signature; `CREATE FUNCTION` / `PROCEDURE` become functions.
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
become import edges (including the `$SCRIPT_DIR/x.sh` and `$(dirname "$0")/x.sh`
idioms), and calls to functions in the same or a sourced file resolve. Shell
gets function-level complexity and no dead-code flagging, because scripts are
invoked by name.

### Lightweight and config files

- Lightweight languages resolve imports against a declared module-name index.
- OpenAPI, Protobuf, GraphQL, Dockerfile, Makefile, YAML, JSON, TOML, Terraform
  and Markdown appear in the file tree and the wiki.
- HTML contributes `<script src>` and `<link href>` edges, so `index.html`
  reaches `src/main.ts` in a Vite or webpack app.
- HTML and QML files are never flagged as dead code, since whether a page or a
  Qt component is reachable is decided at runtime.
- OpenAPI and Protobuf also feed workspace contracts, and Terraform feeds AWS
  Lambda handler edges.
- Godot resource files (`.tscn`, `.tres`, `.escn`, `project.godot`,
  `plugin.cfg`) carry no symbols but are read for the script paths they name.

## Code-health coverage

Health markers need a per-language walker map that is separate from parsing, so
a language can parse fully for the graph and still produce no health findings.
This table is why a language is Full and not Good.

| Language | Complexity / nesting | Class metrics | Assertion smells | Mock saturation | Assertion-free test | Extract Method | Performance risk |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Python | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| TypeScript / JavaScript | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Svelte, Vue, Astro | ✅ | ✅ | ✅ | no | no | ✅ | ✅ |
| Java | ✅ | ✅ | ✅ | no | no | ✅ | ✅ |
| Go | ✅ | n/a | ✅ | no | no | ✅ | ✅ |
| Rust, C++, C# | ✅ | ✅ | ✅ | no | no | ✅ | ✅ |
| Scala, Ruby | ✅ | ✅ | ✅ | no | no | no | ✅ |
| Kotlin | ✅ | ✅ | ✅ | no | no | no | ✅ |
| Dart | ✅ | ✅ | ✅ | no | no | no | ✅ |
| Object Pascal | ✅ | n/a | ✅ | n/a | n/a | no | ✅ |
| Razor | ✅ | n/a | n/a | n/a | n/a | no | ✅ |
| PHP | ✅ | ✅ | ✅ | no | no | ✅ | no |
| C | ✅ | no | no | no | no | ✅ | no |
| F#, Objective-C | ✅ | ✅ | no | no | no | no | no |
| Shell | ✅ | n/a | n/a | n/a | n/a | n/a | n/a |

✅ supported, "no" not available yet, n/a the language cannot carry that metric.

- Dart, F# and Objective-C report class size, method count and CCN (supporting
  detectors like God Class), but do not compute cohesion metrics (LCOM4).
- An unmapped language produces no findings, never guessed ones.
- The two test-quality markers (mock saturation, assertion-free test) are
  advisory and never deduct from a score.

Why Go and Java are held back from the test-quality markers, and why Kotlin's
Extract Method waits on its grammar:
[architecture/language-support.md](../architecture/language-support.md#test-quality-markers-per-language).
Per-marker detail: [CODE_HEALTH.md](CODE_HEALTH.md).

## Limits

- Template dialects (Django/Jinja, Go templates, ERB, Handlebars, Blade,
  Thymeleaf) parse as HTML and yield nothing.
- Svelte and Vue binding forms (`{#each}`, `v-for`) are not resolved, and the
  unused-export pass is off for both.
- Razor has no import edges.
- Scala import resolution is partial, and the Elixir and F# import resolvers
  have known gaps.
- COBOL support is COBOL-85 shaped: `COPY`, dynamic program names and dialect
  extensions produce no edges.
- C has no performance risk support.

The full list per language, with the mechanics behind each gap, is in
[architecture/language-support.md](../architecture/language-support.md#known-gaps-per-language).

## Roadmap

| Language | Next |
|----------|------|
| Vue, Svelte | Template binding forms (`v-for`, `{#each}`) |
| Kotlin | Extract Method, which needs a grammar that labels control flow |
| Scala, Ruby | Extract Method (dataflow dialect) |
| GDScript | Health markers (complexity, performance, dataflow) |
| Object Pascal | A dedicated `uses` resolver |
| COBOL | Copybook resolution, dialect coverage, health markers |
| VB.NET, Elixir | Health markers |
| F#, Objective-C | Health markers beyond complexity |
| SQL / dbt | Column-level blast radius |
| Shell | Shebang detection for extensionless executables |

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

## See also

- [GRAPH.md](GRAPH.md): edge vocabulary, resolution origins, execution flows
- [architecture/language-support.md](../architecture/language-support.md): pipeline internals and per-language mechanics
- [CODE_HEALTH.md](CODE_HEALTH.md): health markers
- [BENCHMARKS.md](../BENCHMARKS.md#accuracy-by-language): measured accuracy per language
- [WORKSPACES.md](../scale/WORKSPACES.md): cross-repo contracts
