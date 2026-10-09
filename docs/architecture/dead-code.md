# Dead-code analysis internals

Contributor reference for the dead-code layer. For what users see and how to
read findings, start with [layers/DEAD_CODE.md](../layers/DEAD_CODE.md). The
algorithm walkthrough in [deep-dives.md](deep-dives.md#1-dead-code-detection)
covers the graph basics; this page records the rules that shape confidence.

All code lives in `packages/core/src/repowise/core/analysis/dead_code/` unless
a path says otherwise.

## Module map

| Module | Role |
|--------|------|
| `analyzer.py` | `DeadCodeAnalyzer`: the four detectors, confidence scoring, caps, rescues. |
| `constants.py` | Never-flag globs, framework decorator lists, dynamic-dispatch name patterns, non-package directories, language sets. |
| `risk_factors.py` | Path risk tokens, `SAFE_CONFIDENCE_THRESHOLD` (0.7), `RISK_CAP_CONFIDENCE` (0.4), `effective_safe_to_delete`. |
| `serving.py` | Read side shared by MCP, REST and workspace merges: `TIER_FLOORS`, filtering, tiering, cross-repo adjustment. |
| `file_reachability.py` | The single "can anything reach this file?" answer. |
| `entry_shape.py` | Files shaped like programs (shebang, Python main guard). |
| `dynamic_markers.py` | Source-text runtime-loader markers per file extension, plus `dynamic_*` graph edges. |
| `name_occurrences.py` | The "is the name written anywhere else?" cap for unused exports. |
| `published_api.py` | Packaged .NET projects: public symbols held at `0.30`. |
| `c_name_uses.py`, `cpp_reachability.py` | C/C++ name uses outside declarations; directory-granular reachability. |
| `contract_methods.py` | COM and runtime contract method names (`QueryInterface`, `AddRef`, `Release`, `IFACEMETHODIMP` / `STDMETHODIMP` implementations). |
| `csharp_reachability.py` | Name-granular reachability for C# (namespaces, not files). |
| `go_reachability.py`, `go_name_uses.py` | Package-granular reachability and package-qualified name uses for Go. |
| `jvm_reachability.py`, `jvm_name_scope.py` | Package-granular reachability and bare-name scope for Java, Kotlin and Scala. |
| `kotlin_multiplatform.py` | `expect` / `actual` pairs. |
| `module_strings.py` | Python modules named by dotted-path strings (entry-point tables, Celery, Django settings). |
| `jsx_prop_guards.py` | JSX components rendered only behind a prop that is never supplied. |

Related: `analysis/finding_registry.py` decides which finding kinds a default
surface shows (`unused_internal` is `hidden`). Storage and triage live in
`persistence/crud/analysis/dead_code.py`. The analyzer runs from
`pipeline/phases/analysis.py` (init), `pipeline/incremental.py` (update), and
the CLI's live path in `cli/commands/dead_code_cmd.py`.

## Flow

1. `analyze(config)` runs the enabled detectors in order: unreachable files,
   unused exports, unused internals, zombie packages. Config keys:
   `detect_*` toggles, `min_confidence`, `dynamic_patterns`, `whitelist`
   (path-exact; no user config feeds it today).
2. Post-passes cap or drop findings (C# scope names, C/C++ name uses, the
   name-occurrence search, unindexed-file names, published API).
3. Findings below `min_confidence` are dropped and counted in
   `hidden_below_threshold`. Init and update store at the `0.4` floor.
4. Persistence replaces `open` rows only. Rows a user marked `acknowledged`,
   `resolved` or `false_positive` survive, and a new finding matching one by
   (file, kind, symbol) is not re-inserted.
5. Read time: `effective_safe_to_delete` re-derives safety. It only ever
   downgrades a stored flag, so rows written under older rules stay honest.

## Unreachable files

Candidate: file-node in-degree 0 after entry points, never-flag paths and the
rescues in `file_reachability.py`. Base confidence comes from git
(`_git_age_confidence`, `_GIT_AGE_RUNGS`):

| Condition | Confidence |
|-----------|-----------|
| No git row at all | `0.50` (`NO_GIT_SIGNAL_CONFIDENCE`) |
| Commits in the last 90 days | `0.40` |
| No commits in 90 days, last commit 365+ days ago | `1.00` |
| ... 180+ days ago | `0.90` |
| ... 90+ days ago | `0.80` |
| File under 30 days old | `0.55` |
| Otherwise | `0.70` |

Caps to `0.40`: a dynamic-import marker in the same directory, any path risk
factor, a namespace-import language (C#). `unreachable_file` is in
`REVIEW_ONLY_KINDS`, so `safe_to_delete` is always false. `lines` is the
physical line count, or unknown when the source was not read.

### Path risk tokens (`risk_factors.py`)

Filename split on `. _ -`; directory segments matched whole.

| Factor | Filename tokens | Directory segments |
|--------|-----------------|--------------------|
| `config` | `config`, `configs`, `configuration`, `conf`, `settings`, `setting`, `setup` | `config`, `configs`, `settings` |
| `environment` | `env`, `environment`, `environ`, `dotenv` | `env`, `environments` |
| `bootstrap` | `bootstrap`, `startup`, `entrypoint` | `bootstrap` |
| `database` | `database`, `db`, `schema`, `seed`, `seeds`, `migration`, `migrations`, `datastore`, `sqlite` | `database`, `db`, `migrations` |
| `script` | none | `scripts`, `bin`, `tasks` |
| `asset` | `sw`, and the pair `service` + `worker` | `public`, `static`, `www` |

Excluded on purpose: broad names (`app`, `main`, `index`, `core`, `base`,
`util`) would cap ordinary modules, and `assets` is the bundled-source
convention in Vite, Vue and Angular.

## Unused exports

Candidate: a public, top-level symbol (methods, fields, properties and enum
members are skipped) with no `imports` edge naming it (or `*`, or a TypeScript
`export { local as alias }`), and no inbound edge in
`REACHABILITY_USE_EDGE_TYPES` (`ingestion/models.py`).

Treated as used without an edge (`_used_without_an_edge`): framework
decorators and their suffixes (`_FRAMEWORK_DECORATORS`,
`_FRAMEWORK_DECORATOR_SUFFIXES`), `@SuppressWarnings("unused")`, names matching
`_DEFAULT_DYNAMIC_PATTERNS` (`*Plugin`, `*Handler`, `*Adapter`, `*Middleware`,
`*Mixin`, `*Command`, `register_*`, `on_*`, `*_view`, `*_endpoint`,
`*_route`, `*_callback`, `*_signal`, `*_task`), framework inner classes
(Python `Meta`), same-file TS/JS type uses, and Python local references.

Base confidence (`_unused_export_confidence`):

- `0.30` if deprecated (name suffix `_DEPRECATED` / `_LEGACY` / `_COMPAT`, or a
  deprecation annotation in any supported form).
- `1.00` when an importer of the file names specific symbols, so absence is
  evidence.
- `0.60` (`UNPROVEN_EXPORT_CONFIDENCE`) otherwise: no importer names symbols (a
  C `#include`, a C# `using`, no importer), C, C++ and Objective-C symbols, C#
  extension-method classes.

Caps to `0.40`: an interface in a file with no `implements` edges, a contract
method, a path risk factor, a dynamic marker in the directory, the name
written elsewhere in the repository (`name_occurrences.py`, including
non-code files; also applied when the search could not run), and the name
appearing in a source file that was skipped during indexing. Deletion-ready
only at `>= 0.7` with no risk factor.

### Language rescues

- **C, C++, Objective-C** (`c_name_uses.py`): a finding is dropped when any
  name its declaration introduces is written outside a declaration, in code or
  in `.def`, `.asm` or `.s` files. Typedefs contribute tag and aliases, enums
  their enumerators. Comments and prose strings do not count. COM methods
  declared with `IFACEMETHODIMP` / `STDMETHODIMP` are never reported.
- **Java, Kotlin, Scala** (`jvm_name_scope.py`): a name written in another
  file in scope, or further down its own file, is a use. Classes named by a
  `build.gradle(.kts)` (`classname`, `implementationClass`, `mainClass`) are
  live.
- **C#**: a file a .NET reference assembly (`ref/*.cs`) lists is never
  reported; packaged projects are held at `0.30` by `published_api.py`.
- **Go**: package-qualified and same-package uses (`go_name_uses.py`).
- **Rust**: `unused_internal` is disabled; rustc's `dead_code` lint covers it.
  Proc-macro entry points are live.
- **Python**: a literal `__all__` raises listed names to `public`; it never
  rescues a finding on its own.

## Unused internals and zombie packages

`unused_internal`: private symbols with no `calls` edge and no cross-file
importer, base `0.65`, never deletion-ready. Hidden on default surfaces by
`finding_registry.py` (measured 0.5% precision, 1,511 findings).

`zombie_package`: a top-level directory with no inter-package import edges,
base `0.50`, never deletion-ready. `_NEVER_PACKAGE_DIRS` skips non-package
directories (`.github`, `.vscode`, `.devcontainer`, `docs`, `examples`,
`scripts`, `assets`, `static`, `public`, `tests`, `benches`, `fuzz` and
similar), and directories holding only Dockerfiles, Makefiles and shell
scripts are run, not imported.

## Never flagged

`never_flag_path` and `_NEVER_FLAG_PATTERNS` in `constants.py` cover: entry
points (`__init__.py`, `__main__.py`, `conftest.py`, `manage.py`, `wsgi.py`,
`asgi.py`, `setup.py`, `main.go`), build files (Gradle, Maven, CMake,
Makefiles, Meson, Bazel, MSBuild, `build.rs`, bundler configs), shell scripts,
files a CI workflow, task file, manifest or script names by path, framework
routes (Next.js, SvelteKit, Nuxt, Remix, Astro, ASP.NET minimal APIs, Blazor and
Razor code-behind), test layouts, generated code (protoc, Qt, Bison/Flex, SWIG,
Cython, Roslyn `*.g.cs`, Dart `*.g.dart`, `**/generated/**`), reflective
loading (Alembic and Django migrations, EF configurations, COM class
factories), vendored trees, build artifacts, and non-code languages from the
language registry. Read the constants for the exact globs; they change more
often than this page.

## Serving

`TIER_FLOORS` in `serving.py` is `{"high": 0.7, "medium": 0.4, "low": 0.0}`,
built from the `risk_factors.py` constants. The TypeScript mirror is
`DEAD_CODE_CONFIDENCE` in `packages/types/src/dead-code.ts`, so CLI, web and
MCP tier identically.

`adjust_cross_repo` runs after tiering in workspace mode: a file with
cross-repo co-change partners gets confidence `* 0.5` and a
`cross_repo_note`; otherwise an `unused_export` in a repo that other repos
depend on as a package gets `* 0.3`.
