# Language Support

**19 languages parsed to a full AST · 35 on the five-rung ladder ·
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
  &nbsp;<strong>· Partial &nbsp;</strong>
  <img src="https://img.shields.io/badge/Luau-00A2FF?style=flat-square&logo=lua&logoColor=white" alt="Luau" />
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
| **Good** (5) | C · Swift · PHP · Dart · Object Pascal | Everything above except the full health suite. Dart and Object Pascal *do* get health markers; C, Swift and PHP don't yet |
| **Partial** (1) | Luau / Roblox | AST symbols and `require()` resolution (Rojo / `.luaurc` aware). No health markers yet |
| | | ⎯⎯ *tree-sitter parsing stops here. The rungs below are derived from git and imports, not from an AST.* ⎯⎯ |
| **Lightweight** (7) | Elixir · Clojure · Haskell · Lean 4 · Erlang · F# · HTML | A real file-to-file import graph, no symbol-level claims |
| **Structural** (9) | Objective-C · R · Zig · Julia · Elm · OCaml · Crystal · Nim · D | Git history only: blame, hotspots, co-change. No AST parsing |

The first three rungs are the **19 languages parsed to a full AST**; all five are
the **35** on the ladder. Both numbers are worth stating and neither is worth
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
| Import resolution | ✅ | ✅ | ✅ | file-level | — |
| Call graph edges | ✅ | ✅ | ✅ | — | — |
| Heritage (extends / implements) | ✅ | ✅ | — | — | — |
| Named bindings | ✅ | ✅ | — | — | — |
| Code-health markers | ✅ | Dart, Pascal | — | — | — |
| Dead code detection | ✅ | ✅ | ✅ | ✅ | ✅ |
| Semantic search & wiki pages | ✅ | ✅ | ✅ | ✅ | ✅ |

Scala's import resolution is partial: it shares the JVM index with Java and
Kotlin and falls back to parsing SBT / Mill build files. Every other Full and
Good language resolves imports outright.

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

A `.svelte` or `.vue` file is three languages at once, so it gets a shared
projection rather than a grammar of its own: a markup grammar locates the
`<script>` blocks and the markup expressions, everything else is blanked to
spaces with newlines preserved, and the result is parsed as TypeScript at
**byte-identical offsets**.

That one property is what makes the rest free: a component reuses the
TypeScript queries, config and all three health dialects verbatim, and every line
number points at the real source file. On top of it: the component itself becomes
a symbol named after the file (`back-to-top.vue` declares `BackToTop`, which is
what a parent actually writes), `<Foo />` in markup mints a call edge, and a
handler referenced only from `on:click={inc}` or `@click="inc"` carries an edge
instead of reading as dead code.

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

Elixir, Clojure, Haskell, Lean 4, Erlang, F# and HTML get a real file-level
import graph: imports extracted per language and resolved against a declared
module-name index. The knowledge graph runs in flow/sparse mode on the result:
honest file-to-file dependencies, no symbol-level claims. F# additionally honours
the fsproj `<Compile Include>` compile order.

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
| Shell | ✅ | n/a | n/a | n/a | n/a |

Every cell is a deliberate call, not an oversight. Go and Object Pascal have no
class metrics because neither language nests a type's method *bodies* inside the
type; mapping them anyway would emit a class with `method_count == 0` for every
class in the repo. Kotlin's Extract Method is **blocked on the grammar**, not
unscheduled: tree-sitter-kotlin parses a bare `break` as a plain identifier, and
a slicer that cannot see a jump would propose an extraction that silently changes
control flow. Rust and C++ omit `string_concat_in_loop` because both append in
amortized O(1), so it would be a guaranteed false positive.

Per-marker mechanics, every per-language precision ceiling and the reasoning
behind each `n/a`: [CODE_HEALTH.md](CODE_HEALTH.md) and
[architecture/language-support.md](../architecture/language-support.md).

---

## Known ceilings

Stated plainly, because a ceiling you know about is worth more than a claim you
cannot check.

- **Template dialects are invisible.** Django/Jinja, Go templates, ERB,
  Handlebars, Blade and Thymeleaf parse cleanly as HTML and yield nothing:
  `{% extends "base.html" %}` is plain text to an HTML parser. Covering them
  needs a per-dialect regex tier gated on a framework manifest.
- **Svelte and Vue binding forms are skipped.** `{#each x as y}` and
  `v-for="x in xs"` *parse* as JS but mean something else, so they are skipped: a
  parse that succeeds with the wrong meaning is worse than a skip. Component
  props are set by the parent as markup attributes and never imported by name, so
  the unused-export pass is suppressed for both.
- **Object Pascal type kinds collapse.** `record` / `interface` / class-helper /
  enum / type alias all report as `kind="class"`, and its `extends` vs
  `implements` split is inferred from the `I`-prefix naming convention rather
  than guaranteed by the language.
- **C has no health dialect.** It shares the C++ grammar for parsing but reaches
  no health walker map, so it gets graph coverage without markers.
- **Scala import resolution is partial**, and ScalaTest's infix DSL
  (`x shouldBe y`) is not counted as an assertion: there is no assert-prefixed
  callee to key on.

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
| Object Pascal | Good | Assertion and performance markers, a dedicated `uses` resolver |
| Elixir · F# | Good | AST upgrade (both grammars are available on PyPI) |
| SQL / dbt | — | Column-level blast radius |
| Shell | — | Shebang detection for extensionless executables |
| HTML | Lightweight | A regex import tier for template dialects, gated on a framework manifest |

---

## See also

- **[GRAPH.md](GRAPH.md)** · the graph itself: edge vocabulary, resolution origins, execution flows
- **[architecture/language-support.md](../architecture/language-support.md)** · pipeline internals, the call-resolution architecture, and the contributor recipe for adding a language
- [CODE_HEALTH.md](CODE_HEALTH.md) · health markers and per-language precision
- [WORKSPACES.md](../scale/WORKSPACES.md) · cross-repo contracts and co-change
