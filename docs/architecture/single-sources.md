# One owner per concept

Each concept repowise decides (is this file a test, which unit owns it, what a
commit id looks like) is decided in exactly one module. Every other call site
asks that module. It never re-derives the answer from a path, a regex or a
copied list.

The reason is correctness. When two copies of a rule exist, they drift: one
gets a fix and the other does not. The result is two surfaces giving
different answers about the same file. A file counted as production in one
place and as a test in another, a commit id one validator accepts and another
rejects, an excluded file that one graph view hides and another uses as a
hop. A single owner makes that class of bug impossible: there is one answer,
and a fix lands everywhere at once.

This page lists the owners, what to do when one is missing a case, and the
concepts that still have more than one home.

Module paths below are relative to `packages/core/src/repowise/core/` unless
they start with `server/`, which means `packages/server/src/repowise/server/`.

## Owners

| Concept | Owner and entry points | What it decides | Callers must not |
|---|---|---|---|
| File origin | `code_origin.py`: `code_origin(path, ...)`, `path_origin(path)`, `owes_a_test(path, origin)`, `is_build_file(path)`, `is_benchmark_path(path)`, `is_benchmark_function(path, name)` | One of `production`, `test`, `generated`, `vendored`, `build`, `docs_example`, `tooling`, in fixed precedence. Stored per file as `health_file_metrics.code_origin` | Re-derive origin from a path. Read the stored `code_origin`; call `path_origin()` only when no stored value exists. Any rule that judges files (untested, dead, health, risk, fix lists) goes through this |
| Test paths | `test_paths.py`: `is_test_path`, `is_test_support_path`, `is_test_related_path` | Whether a path is a test, test support (`conftest.py`), or either. Patterns come from the language registry. Stamped once at ingestion as `FileInfo.is_test` and `GraphNode.is_test` | Call the path functions when a `FileInfo` or graph node is at hand: read its `is_test` flag. Never write a local test-name regex |
| Manifest names, by role | `ingestion/languages/registry.py` (`REGISTRY`): `is_package_manifest(path)`, `is_manifest(path)`, `matches_package_manifest_glob(name)`, `package_manifest_filenames()`, `manifest_filenames()`. Declared per language on `LanguageSpec` (`ingestion/languages/spec.py`): `manifest_files`, `manifest_globs`, `build_config_manifests`, `lock_files` | Which file names are package manifests (mark a unit root), build config (configure a build, mark nothing) and lockfiles | Hand-write a list of manifest names. A reader that needs extra non-manifest names (a `Dockerfile`, a runner config) adds them on top of a registry set |
| Package roots on disk | `ingestion/package_roots.py`: `scan_package_manifests(repo_path, ...)` | Each directory holding a package manifest, from a pruned directory scan driven by the registry | Walk the tree looking for manifests itself |
| Build units and file ownership | `build_units/`: `collect.collect_build_units(ctx)` during a graph build, `persistence/crud/build_units.load_build_units(session, repo_id)` from the store; `BuildUnitIndex.units_owning(path)`, `.unit(id)`, `.dependents(id)` | The projects, crates, packages and modules a repo builds, their in-repo dependencies, and which units own each file. See [build-units.md](build-units.md) | In new code, read workspace members, `go.mod`, `*.csproj` or `pom.xml` to answer "which unit owns this file" or "what depends on this unit". Resolvers keep reading their own manifests to resolve imports |
| Test runners | `test_runners/`: `LANGUAGE_SUFFIXES`, `is_code_file(path)`, `resolve_runner`, `runner_args`, `left_out`; `test_runners/packages.py`: `runs_for_tests(root, manifests, tests)`, `js_root_runs(tests)`; `test_runners/stacks.py`: `detect_stacks(paths, files, units)`, `detect_repo_stacks(repo_path, units)`, `Stack` | Which runner takes a test file, the arguments a selection gives one runner, the command that runs a test's package from the repository root (`go -C`, `cargo test -p`, Maven, Gradle, RSpec or Rake, `dotnet test`, `npm --prefix`), and each build unit's test command with coverage, its prerequisites and report paths | Write a suffix-to-runner table, a coverage-flag table or a stack detector, or build a runner command line itself |
| Skip directories | `fs_walk.py`: `PRUNED_DIRS`, `PRUNED_DIRS_DERIVED`, `POLICY_BLOCKED_DIRS`, `INDEX_BLOCKED_DIRS`; `walk_repo(root, ...)`, `iter_glob(...)` | Never-source directories (VCS metadata, caches, package stores), directories the index leaves out by policy (`vendor`, `fixtures`), and their union, which the traverser uses | Add a skip-directory set or call `Path.rglob` over the repo. A walk that needs more (`bin` for .NET) adds its extras on top of one of these sets. A guard test fails on any new direct `rglob` in `packages/core` |
| Commit id shape | `git_sha.py`: `is_full_sha(value)`, `is_sha_prefix(value, min_len=7)`, `FULL_SHA_PATTERN`, `SHA_PREFIX_PATTERN` | A full id is 40 (SHA-1) or 64 (SHA-256) lowercase hex digits; a prefix is 7 to 64 | Write a SHA regex. A caller scanning free text builds its regex from the exported pattern strings and adds its own anchors |
| Symbol ids | `ingestion/symbol_identity.py`: `make_symbol_id(path, *names)`, `module_symbol_id(path)`, `file_of_symbol(symbol_id)`, `split_symbol_id`, `base_symbol_id` | The `<path>::<parent>::<name>` grammar, the `__module__` symbol that owns top-level code, and overload or variant discriminators | Join `"::"` by hand, spell `"::__module__"`, or take the file with `split("::", 1)[0]` |
| Node id prefixes | `ids.py`: `parse(raw)`, `render(node)`, `is_external(raw)`, `file_path_of(raw)` | The `file:`, `external:`, `pkg:`, `cmp:` prefixes and how `::` reads inside a prefixed id | Split or `startswith` a node id by hand |
| Line to enclosing symbol | `symbol_spans.py`: `symbol_at(spans, line)`, `symbols_overlapping(...)`, `innermost(spans)` | Which symbol span holds a line, over spans the caller already has | Loop over spans comparing line ranges |
| Query-time exclusion | `exclusion.py`: `build_exclude_spec(repo_path)`, `is_excluded(path, spec)`, `decision_is_excluded(row, spec)` | Which stored rows a read path hides: config `exclude_patterns` plus the root `.gitignore` and `.git/info/exclude`, cached per repo and rule-file stamp | Compile its own pathspec from `exclude_patterns`. The MCP helper `server/mcp_server/_helpers.is_excluded` delegates here |
| Server code graph | `server/services/graph_views.py`: `load_code_graph(session, repo_id, exclude_spec)` | The repo's code nodes and edges as one `DiGraph`, minus excluded files and non-code relations, cached per index state and exclude rules | Select every edge from SQL and build a graph per request. Callers must not mutate the returned graph |
| TOML reading | `ingestion/workspace_members.py`: `read_toml(path)`, `read_toml_or_none(path)` | Parse a TOML file; `{}` or `None` when it cannot be read, decoded or parsed | Open and `tomllib.load` a manifest with its own error handling |
| Content hash | `ingestion/models.py`: `compute_content_hash(source)` | The sha256 a file's bytes are keyed by, computed once at ingestion | Hash the same bytes again with another algorithm for a cache key |
| Edge-type sets | `ingestion/models.py`: `EdgeType`, `FILE_DEPENDENCY_EDGE_TYPES`, `SYMBOL_USE_EDGE_TYPES`, `EXECUTION_EDGE_TYPES`, `NON_CODE_EDGE_TYPES` | Which edge types count as a file dependency, a symbol use, an execution step, or non-code | Re-type the full sets. Named subsets that are still local are listed under [planned consolidations](#planned-consolidations) |
| Health bands | `analysis/health/grading.py`: `band_for(score)`, `BAND_LABEL`, `BAND_ORDER` | The score cut-offs and labels for health bands. The TypeScript mirror in `packages/types/src/health.ts` is parity-tested | Compare a score against literal cut-offs |
| Glob matching for user-written patterns | `analysis/health/config.py`: `_compile_glob(pattern)` | What a path glob a user writes matches, with gitignore semantics: a lone `*` stops at one directory, `**/x` also matches `x` at the root, a pattern without a wildcard covers the subtree under it. Health rules, exclude patterns, `generate --path` and `workspace scan --exclude` all go through it | Match a user glob with `fnmatch`, where `*` crosses `/` and the same pattern selects different files than it does elsewhere ([#5779](https://github.com/repowise-dev/repowise/issues/5779)) |

## Adding to an owner

When an owner is missing a case, extend the owner. Do not copy its logic
next to your call site with the extra case added: that copy is the next drift
bug.

- **Look first.** Search the codebase for the concept (a Repowise
  `search_codebase` or `get_answer` call, then grep), and check the table
  above. A module docstring that says "single home" or "the one owner" is the
  owner.
- **Extend in place.** Add the case, a parameter or a small query function to
  the owner module, with a test next to its existing tests. A language-specific
  convention usually belongs as data on its `LanguageSpec`, not as code.
- **Keep owners import-light.** Owners are imported from ingestion, analysis,
  the server and the CLI. `git_sha.py` and `ids.py` import nothing from the
  package; `build_units/__init__.py` exposes only the model, so a reader of
  stored units does not load the resolvers. A new dependency in an owner is a
  new dependency for every caller.
- **Move callers, then delete the copy.** If you find a second copy while
  extending an owner, route its callers through the owner in the same change
  when it is small, or file an issue and add it to the list below.
- **New concept, new owner.** If nothing owns the concept yet, put it in one
  module with a docstring that says it is the owner, and add a row here.

## Planned consolidations

These concepts are still decided in more than one place. Each is a
self-contained piece of work; the linked issues describe the copies and the
expected behaviour.

| Concept | Where the copies are | Target owner | Issue |
|---|---|---|---|
| PageRank percentile | The REST Files table, the REST file page, the MCP graph utilities (`server/mcp_server/_graph_utils.percentile_rank`) and `get_context` signals each compute it, with different formulas | One percentile function used by REST and MCP | [#5745](https://github.com/repowise-dev/repowise/issues/5745), [#5774](https://github.com/repowise-dev/repowise/pull/5774) |
| Per-file size caps | The traverser, change health, diff risk and the MCP file preview each define their own byte limit | The traverser's source ceiling, imported | [#5780](https://github.com/repowise-dev/repowise/issues/5780) |
| Package of a file | `ingestion/package_roots.py` `module_for` and `owning_package` answer it from manifest roots, beside the build-unit index | `BuildUnitIndex.units_owning` | |
| Test status from paths | Analysis and MCP call sites re-derive test status from the path where a stored `is_test` flag exists | The stored flag, with `test_paths` as the path fallback | [#3235](https://github.com/repowise-dev/repowise/issues/3235), [#3236](https://github.com/repowise-dev/repowise/issues/3236) |
| Git runner and log parsing | Many modules call `subprocess` or GitPython directly, each with its own `--format` dialect, merge and rename flags | `analysis/git_cli.run_git` (stdin closed, UTF-8, timeout), moved onto lane by lane | [#3919](https://github.com/repowise-dev/repowise/issues/3919) |
| Entry points | `entry_candidacy.py` decides candidacy and `generation/entry_points.py` orders, while execution flows, KG curation and a SQL ranking keep their own notions | `entry_candidacy` decides, `entry_points` orders | |
| Token estimate | `len(text) // 4` is restated in several modules, and savings rows carry two estimator tags | `distill/budget.estimate_tokens` and one tag constant | |
| Edge-type subsets | `{imports, type_use}`, heritage `{extends, implements}` and negated lists are typed locally in several modules | Named subsets in `ingestion/models.py` | |
| UI formatting helpers | Compact numbers, relative time, ages and `basename` are reimplemented across `packages/ui` and the VS Code webview | `packages/ui/src/lib/format.ts` | [#5784](https://github.com/repowise-dev/repowise/issues/5784) |
