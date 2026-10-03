# Dead Code

Repowise reports the files nothing imports, the exported symbols nothing uses,
and the packages nothing depends on. Each finding carries a confidence score,
a deletion-readiness flag and the evidence behind both. The analysis is graph
traversal plus git history: it needs no LLM key, makes no network calls, and
runs as part of `repowise init` and `repowise update`.

Treat every finding as a candidate. Static reachability can show that nothing
*imports* a file; it cannot show that nothing *loads* it. The confidence
scores, caps and exemptions below all follow from that gap.

## Quick start

```bash
repowise init                     # findings are computed during indexing
repowise dead-code                # the report
repowise dead-code --safe-only    # deletion-ready findings only
repowise dead-code --kind unused_export --format json
```

```
 Kind              File / Symbol            Confidence  Ready?  Lines  Reason
 unused_export     formatLegacyDate               100%    ✓       14  Public symbol 'formatLegacyDate' has no importers
 unreachable_file  src/legacy/parser.ts           100%    ✗      212  File has no importers (in_degree=0)
 unreachable_file  config/feature_flags.py         40%    ✗       31  File has no importers (in_degree=0)

Cleanup-candidate lines: 14 (high 2, medium 1 confidence)
```

From an agent, `get_dead_code()` or `get_dead_code(tier="high", safe_only=True)`.
In the dashboard, open the **Dead code** view of a repository.

## What it finds

| Kind | Meaning | Shown by default |
|------|---------|------------------|
| `unreachable_file` | No file in the repository imports this file, and it is not an entry point. | Yes |
| `unused_export` | A public, top-level symbol that no import, call, inheritance or type reference reaches. | Yes |
| `zombie_package` | A top-level package that no other package in the repository imports. | Yes |
| `unused_internal` | A private or underscore-prefixed symbol that nothing calls. | No, opt in |

Methods, fields, properties and enum members are never reported as unused
exports: they are reached through their container. `unused_internal` is
withheld from the dashboard and default MCP responses because its measured
precision was very low ([Accuracy and limits](#accuracy-and-limits)); ask for
it with `--include-internals` or `include_internals=True`.

## Reading the results

### Confidence

Confidence runs from `0.0` to `1.0` and measures how strong the evidence for
deadness is. It starts from a base value per kind and can only be lowered:

- **Unreachable files** are scored from git activity. A file untouched for a
  year or more starts at `1.00`; one still being committed to starts at `0.40`;
  a file under 30 days old starts at `0.55`, because it is often unfinished.
- **Unused exports** start at `1.00` when the file has importers and none of
  them takes this symbol. When that check is impossible (no importer names
  individual symbols, or the language reaches symbols through a preprocessor)
  the start is `0.60`. Deprecated symbols start at `0.30`.
- **Unused internals** start at `0.65`. **Zombie packages** start at `0.50`.

Several caps then pull a finding down to `0.40`, the review tier: a runtime
loader in the same directory, a path that looks like config or bootstrap code,
an unused export's name written somewhere else in the repository, or a name that
appears in a file repowise could not index. Each cap adds a line to the
finding's evidence saying why.

### Tiers

| Tier | Confidence | Meaning |
|------|-----------|---------|
| High | `>= 0.7` | No references found. Strong cleanup candidate. |
| Medium | `0.4` to `0.7` | Likely unused, but something indirect may reach it. Review first. |
| Low | `< 0.4` | Plausibly used at runtime (dynamic loading, reflection, published API). Investigate first. |

The CLI, the dashboard and `get_dead_code` use the same tier floors, so a
finding lands in the same tier on every surface. The default `min_confidence`
is `0.4` everywhere, which hides the low tier unless you ask for it.

### `safe_to_delete`

A finding is deletion-ready (the CLI's "Ready?" column) only when its
confidence is `0.7` or higher and its path carries no runtime-load risk factor
(config, environment, bootstrap, database, script or served-asset paths).
Unreachable files, zombie packages and unused internals are never marked
deletion-ready, whatever their confidence. In practice `safe_to_delete` means
"an unused export with strong evidence".

### Exempt by construction

Some files and symbols are never reported, because static reachability is the
wrong tool for them. This covers entry points and programs (`__main__.py`,
`main.go`, files with a shebang), build and CI files and anything they name by
path, test files, generated code, vendored trees, framework routes such as
Next.js `page.tsx`, and symbols registered by a framework decorator or
annotation (pytest fixtures, Flask and FastAPI routes, Celery tasks, Spring
stereotypes, and similar). Names that follow dynamic-dispatch conventions
(`*Handler`, `*Plugin`, `register_*`, `on_*` and others) are treated as used.
The full lists live in [the internals doc](../architecture/dead-code.md).

### Empty results

An empty report means no finding cleared the confidence floor, not that the
repository has no dead code. The CLI prints how many findings were hidden below
the floor. An index whose dead-code pass failed also reads as empty; pass
`--min-confidence 0.0` to compute live.

## Tuning and suppressing

- **Confidence floor.** `--min-confidence` (CLI) or `min_confidence` (MCP).
  The index stores findings at `0.4` and above, so the CLI answers from the
  index at that floor or higher and computes a fresh analysis below it.
- **Scope.** `--kind`, `--no-unreachable`, `--no-unused-exports`,
  `--include-internals`, `--no-include-zombie-packages`. Passing `--kind`
  overrides the individual toggles.
- **Triage in the dashboard.** Each finding has a status: `open`,
  `acknowledged`, `resolved` or `false_positive`. Set it per row or resolve in
  bulk. A finding you have acted on stays out of the open list across later
  `repowise update` runs; it is not re-opened.
- **Excluding paths.** `.repowiseIgnore` files and `exclude_patterns` in
  `.repowise/config.yaml` remove paths from indexing (see
  [CONFIG.md](../reference/CONFIG.md)). An excluded file's own imports leave
  the graph too, so a file only it imported can then surface as unreachable.
- **In source.** A Java or Kotlin symbol annotated `@SuppressWarnings("unused")`
  is treated as deliberately unused and is not reported. A symbol marked
  deprecated (by annotation, or by a `_DEPRECATED`, `_LEGACY` or `_COMPAT`
  name suffix) drops to `0.30`, below the default floor.
- **Turning the MCP tool off.** `mcp: {tools: ["-get_dead_code"]}` in
  `.repowise/config.yaml`.

There is no allowlist file for dead-code findings; use the dashboard status.

## False positives and dynamic code

When a file contains a runtime loader, repowise assumes its neighbours in the
same directory may be reached through it and caps their confidence at `0.40`.
Markers are recognised in Python, JavaScript and TypeScript, Java, Kotlin,
Ruby, PHP, Go, Swift, Scala, Rust, C# and C/C++. Examples: `importlib.import_module`,
dynamic `import(` and `require.context(`, `Class.forName(`, `ServiceLoader.load(`,
`const_get(`, `plugin.Open(` and `//go:embed`, `NSClassFromString(`,
`Activator.CreateInstance(`, and `dlopen(` / `LoadLibrary(` in C. Dynamic edges
found by the language extractors count the same way.

The cases where a finding is most likely wrong:

- **Reflection and string-keyed dispatch** that matches no marker, naming
  convention or framework hint: a class named in a YAML file, a handler looked
  up in a registry dict.
- **Entry points the graph did not recognise**, such as a serverless handler or
  a binary target with an unusual layout. A whole directory lighting up is
  usually this.
- **Barrel re-exports.** Barrel files are exempt as unreachable files, but a
  symbol re-exported through one for external callers can surface as an
  unused export.
- **Published libraries.** Code consumed outside the repository has no
  importer inside it. Packaged .NET projects are detected and held at `0.30`;
  other ecosystems are not.
- **Test-only usage counts as usage.** A symbol only its tests import is not
  reported. There is no "used only in tests" classification.

The evidence list on each finding says which signals applied. Read it before
deleting anything.

## Workspaces

In a workspace, a file dead inside its own repo may be the surface another repo
depends on. `get_dead_code` checks each finding against the cross-repo data
before returning it:

- If the file changes together with files in other repos (git co-change),
  confidence is halved and the finding gains a `cross_repo_note` naming those
  repos.
- Otherwise, if the finding is an unused export and another repo depends on
  this one as a package, confidence is cut to 30% of its value with a note to
  verify consumers.

The adjustment runs after tiering, so it lowers the displayed confidence
without moving a finding between tiers. `repo="all"` merges every workspace
repo and tags each finding with its alias. The CLI has no cross-repo pass:
`--repo <alias>` analyzes one repo in isolation (the primary repo by default).
See [WORKSPACES.md](../scale/WORKSPACES.md) for how cross-repo data is built.

## Accuracy and limits

- `unused_internal` was measured at under 1% precision on 1,511 hand-labelled
  findings from one TypeScript/Python monorepo, which is why it is withheld by
  default. Private symbols used within their own file are often invisible to
  the graph.
- Whole unreachable files were measured well below the deletion-ready bar
  (build scripts, manifests and runtime loaders read files by path), so they
  are never marked `safe_to_delete`.
- Unused-export confidence is only as good as the graph's call and import
  resolution for that language. `get_dead_code` reports this per language in
  `summary.call_resolution_basis`.
- Languages without dynamic-import markers get no dynamic-import cap.
- Rust private items are never reported: rustc's `dead_code` lint covers them
  with type and macro information this analysis lacks.
- The name search that caps unused exports counts a name written only in a
  comment as a use. This trades recall for precision.

For benchmark method across layers, see [BENCHMARKS.md](../BENCHMARKS.md).

## Where it shows up

- **CLI** `repowise dead-code`; **MCP** `get_dead_code` (on by default).
- **Dashboard:** the Dead code view (tiers, a safe-to-delete pile, an owner
  leaderboard, status triage) and the dead-code signal on the graph view.
  Module health includes each module's dead-code share; contributor profiles
  carry dead-code files and lines per owner.
- **Editor:** the VS Code extension's findings tree.

## Reference

### `repowise dead-code [PATH]`

| Flag | Default | Description |
|------|---------|-------------|
| `--min-confidence FLOAT` | `0.4` | Minimum confidence. Below `0.4` the analysis runs live. |
| `--safe-only` | off | Only deletion-ready findings. |
| `--kind KIND` | all | `unreachable_file`, `unused_export`, `unused_internal` or `zombie_package`. Overrides the toggles below. |
| `--format` | `table` | `table`, `json` or `md`. |
| `--include-internals` / `--no-include-internals` | off | Report unused private symbols. |
| `--include-zombie-packages` / `--no-include-zombie-packages` | on | Report packages nothing else imports. |
| `--no-unreachable` | off | Skip unreachable-file findings. |
| `--no-unused-exports` | off | Skip unused-export findings. |
| `--repo ALIAS` | primary repo | Workspace mode: analyze this repo. |
| `--no-workspace` | off | Force single-repo mode inside a workspace. |

The CLI reads stored findings when the index is at `HEAD` and the request fits
what was stored; otherwise it analyzes the working tree live, and says which.
JSON rows carry `kind`, `file_path`, `symbol_name`, `confidence`, `reason`,
`safe_to_delete`, `risk_factors`, `lines` and `primary_owner`. Full command
reference: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-dead-code-path).

MCP parameters and response shape: [`get_dead_code` in MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_dead_code).

## See also

- [architecture/dead-code.md](../architecture/dead-code.md): exemption lists,
  risk tokens, per-language rescues and confidence scoring internals.
- [CODE_HEALTH.md](CODE_HEALTH.md): the health layer, built on the same graph
  and git data.
- [GRAPH.md](GRAPH.md): the dependency graph the analysis walks.
- [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md): which languages parse into that graph.
