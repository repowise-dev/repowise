# Dead Code

Repowise reports the files nothing imports, the exported symbols nothing uses,
and the packages nothing depends on, each with a confidence score and the
evidence behind it. Pure graph traversal and SQL: no LLM calls, no network, and
it finishes in under 10 seconds on any repo size.

The layer surfaces candidates. You decide. Static reachability can prove that
nothing *imports* a file; it can never prove nothing *loads* it. Everything below
is built around that asymmetry.

## Quick start

```bash
repowise init                                    # dead-code findings populate during indexing
repowise dead-code                               # the report
repowise dead-code --safe-only --min-confidence 0.8   # cleanup-ready only
repowise dead-code --kind unused_export --format json
repowise dead-code --repo backend                # workspace, one repo
```

```
repowise dead-code

  23 findings · 4 safe to delete

  ✓ utils/legacy_parser.ts          file      1.00   safe to delete
  ✓ auth/session.ts                 file      0.92   safe to delete
  ✓ helpers/formatDate              export    0.71   safe to delete
  ✗ analytics/v1/tracker.ts         file      0.41   recent activity, review first
```

From an agent:

```python
get_dead_code()
get_dead_code(min_confidence=0.8, tier="high", safe_only=True)
get_dead_code(kind="unused_export", group_by="owner")
```

## The four finding kinds

| Kind | What it means | How it is computed | Base confidence |
|------|---------------|--------------------|-----------------|
| `unreachable_file` | No file in the repo imports this one. | File-node in-degree of 0 on the dependency graph, after entry points and the never-flag allowlist are removed. | Scored from git age (below) |
| `unused_export` | A public symbol nothing imports. | No `imports` edge names the symbol (or `*`, or a TypeScript `export { local as alias }` rename), and no `calls` / `method_implements` / `reads` / `extends` / `implements` / `type_use` edge reaches it. | `1.00` when the containing file *does* have importers (so the file is alive and only this symbol is not), `0.70` when it does not, `0.30` when the name ends in `_DEPRECATED` / `_LEGACY` / `_COMPAT` |
| `unused_internal` | A private or underscore-prefixed symbol nothing calls. | No `calls` edge, and no cross-file importer pulls the name (which would mean a dispatch-table lookup). Off by default. | `0.65` |
| `zombie_package` | A whole top-level package no other package imports. | No inter-package import edges into it. Never marked safe to delete. | `0.50` |

`unused_internal` is opt-in (`--include-internals`); `zombie_package` is on by
default and can be turned off with `--no-include-zombie-packages`. Passing
`--kind` overrides both toggles, so `--kind unused_internal` enables internals on
its own.

Two caveats on the numbers. `unused_internal` is disabled entirely for Rust,
where the graph does not yet emit intra-file call edges, so a private Rust helper
is never flagged. And the `lines` count on file and package findings is an
estimate (symbol count times ten), not a real line count, so treat the
"reclaimable lines" roll-up as an order of magnitude rather than a figure.

### How unreachable-file confidence is scored

An orphaned file that nobody has touched in a year is a much stronger signal than
one added last week. Confidence starts from git activity:

| Condition | Confidence |
|-----------|-----------|
| No commits in 90 days, last touched over a year ago | `1.00` |
| No commits in 90 days, last touched over 180 days ago | `0.90` |
| No commits in 90 days, last touched over 90 days ago | `0.80` |
| No commits in 90 days, no other signal | `0.70` |
| No commits in 90 days, but the file is under 30 days old | `0.55` (may be work in progress) |
| Still being committed to | `0.40` |

Then it only ever goes down. Two caps apply:

- **Dynamic imports nearby.** If any file in the same directory uses a runtime
  loader, confidence is capped at `0.40`.
- **Runtime-load risk factors.** If the path looks like config, environment,
  bootstrap, database, or script code (`config`, `settings`, `env`, `bootstrap`,
  `startup`, `entrypoint`, `database`, `db`, `schema`, `seed`, `migration`, or a
  `scripts/` / `bin/` / `tasks/` directory), confidence is capped at `0.40` and
  the finding carries an evidence line explaining why. These are exactly the
  files wired up by a config key or a string path rather than an import, so
  "nothing imports it" is weak evidence.

## Confidence tiers and `safe_to_delete`

A finding is presented as **safe to delete** only when confidence is at or above
`0.70` **and** the path carries no runtime-load risk factor **and** the name does
not match a dynamic-dispatch pattern (`*Plugin`, `*Handler`, `*Adapter`,
`*Middleware`, `*Mixin`, `*Command`, `register_*`, `on_*`, `*_view`,
`*_endpoint`, `*_route`, `*_callback`, `*_signal`, `*_task`). Zombie packages are
never safe to delete regardless of confidence.

The safety re-derivation is monotonic: it only ever downgrades a stored flag,
never upgrades it, so findings written by an older version stay honest.

The two surfaces bracket the tiers differently, which is worth knowing before you
compare numbers:

| Surface | High | Medium | Low | Default floor |
|---------|------|--------|-----|---------------|
| CLI (`repowise dead-code`) | `>= 0.7` | `0.5` to `0.7` | `< 0.5` | `--min-confidence 0.5` |
| MCP (`get_dead_code`) | `>= 0.8` | `0.5` to `0.8` | `< 0.5` | `min_confidence=0.5` |

Both surfaces now share the same default floor (`0.5`). The MCP high/medium
cutovers stay stricter: an agent acting on a finding is riskier than a human
reading a table.

## What is exempt by construction

Before anything is scored, repowise removes what it knows is framework-loaded,
generated, or convention-wired. Static reachability is simply the wrong tool for
these, so they are never flagged rather than flagged and down-weighted.

| Group | Examples |
|-------|---------|
| Entry points | Anything the graph marked `is_entry_point`, plus `__init__.py`, `__main__.py`, `conftest.py`, `manage.py`, `wsgi.py`, `asgi.py`, `setup.py`, `main.go`, `build.rs` |
| Shell scripts | `*.sh`, `*.bash`, `*.zsh`. Invoked by name from CI configs and Makefiles; static reachability is meaningless |
| Framework routes | Next.js `page.tsx` / `layout.tsx` / `route.ts` / `middleware.ts`, SvelteKit `+page.svelte`, Nuxt `pages/*.vue`, Remix entry files, ASP.NET minimal-API `Apis/` / `Endpoints/`, Blazor and Razor code-behind |
| Test files | `*_test.go`, `*.test.ts`, `*.spec.ts`, `*_test.cc`, `*Test.java`, `**/tests/*.rs`, `src/test/java/`, MSTest and xUnit project layouts, `__tests__/`, `__mocks__/` |
| Generated code | protoc `*.pb.go` / `*.pb.cs` / `*.pb.cc`, Qt MOC/UIC/RCC, Bison/Flex, SWIG, Cython, stringer, MapStruct `*MapperImpl.java`, Dagger, AutoValue, Roslyn `*.g.cs`, Dart `*.g.dart` / `*.freezed.dart`, `**/generated/**` |
| Reflective loading | Alembic `versions/*.py`, Django migrations, EF entity configurations, COM `*ClassFactory.cpp`, Win32 `*NativeMethods.cs`, ETW event classes |
| Vendored trees | `vendor/`, `third_party/`, `deps/`, `external/`, `extern/`, `contrib/`, `submodules/` |
| Build artifacts | `build/`, `cmake-build-*/`, `_deps/`, `*.min.js`, `*.bundle.js` |
| Non-code languages | Config and infra languages from the language registry, plus anything the parser could not identify |

Symbols decorated by a framework are treated as live too: pytest fixtures, Flask
and FastAPI routes, Django `admin.register` and signal receivers, Celery tasks,
Click and Typer commands, and the JVM stereotype and routing annotations
(`@Component`, `@Service`, `@RestController`, `@Entity`, `@KafkaListener`,
`@GetMapping`, `@Test`, JAX-RS `@Path` / `@GET`). Decorator *suffixes* are matched
too, so `@my_local_group.command` and `@api.get` register even when the receiver
has a project-local name.

Two more targeted rescues: an `interface` in a file with no incoming `implements`
edges is capped at `0.40` (implementor detection is heuristic, and missing
evidence is not evidence of absence), and COM contract methods
(`QueryInterface`, `AddRef`, `Release`) are capped the same way because they are
dispatched through native vtables.

Zombie-package detection additionally ignores directories that are not packages
at all: `.github`, `.vscode`, `.devcontainer`, `docs`, `scripts`, `assets`,
`static`, `public`, `tests`, `benches`, `fuzz`, and their siblings.

## Dynamic-import awareness

When a file uses a runtime loader, repowise assumes its neighbours may be reached
through it and caps their confidence at `0.40`. Detected markers include:

- **Python**: `importlib.import_module`, `__import__(`, `importlib.reload`, `pkgutil.iter_modules`
- **JS/TS**: dynamic `import(`, `require.context(`, `import.meta.glob(`,
  `React.lazy(`, `next/dynamic`, `jest.mock(` / `vi.mock(`, and the
  `'use server'` / `'use client'` boundary directives

This is a text scan over source, grouped by file extension. Languages without
markers in the table get no dynamic-import protection, which is one of the honest
limits below.

## Workspaces: cross-repo consumers

In a workspace, a file that is dead inside its own repo may still be the surface
another repo depends on. `get_dead_code` checks every finding against the
cross-repo graph before returning it:

- **The file cross-changes with files in other repos.** Confidence is halved and
  the finding gains a `cross_repo_note` naming those repos. This is a behavioral
  signal drawn from git co-change history, not an import edge, so read it as
  "something over there moves when this moves".
- **Another repo depends on this one as a package**, the finding is an
  `unused_export`, and there was no co-change signal. Confidence is cut to 30% of
  its value with a note saying the export may be consumed and should be verified.

The adjustment runs after tiering, so it lowers the displayed confidence without
moving a finding between tiers. `repo="all"` aggregates every workspace repo,
sorts by confidence then size, and tags each finding with its repo alias.

The CLI has no cross-repo pass yet: `repowise dead-code --repo <alias>` analyzes
that one repo in isolation. See [WORKSPACES.md](../scale/WORKSPACES.md) for how
the cross-repo graph is built.

## Known false-positive sources

The layer is conservative, but it is still a static analysis over a static graph.
These are the cases where a finding is most likely wrong:

- **Reflection and string-keyed dispatch.** A class instantiated from a name in a
  config file, a handler looked up in a registry dict, a Java class loaded by
  `Class.forName`. The dynamic-pattern name list and the `.register` decorator
  suffix catch the common shapes; nothing catches all of them.
- **Dynamic imports in unmodelled languages.** The marker table covers Python and
  JS/TS. Go, Ruby, PHP, Kotlin, Swift, and Scala runtime loading is not detected
  yet, so an orphan in those languages carries no dynamic-import cap.
- **Entry points the graph did not mark.** A binary target, a CLI script, or a
  serverless handler that neither the allowlist nor the entry-point pass
  recognized reads as unreachable every time. If you see a whole directory light
  up, that is usually the cause.
- **Barrel re-exports.** `__init__.py` and index barrels are exempt from being
  flagged as unreachable *files*, but they are deliberately not exempt in the
  unused-export pass: a symbol defined in a barrel that nobody imports should
  still be reported. A symbol re-exported through a barrel to external callers
  can therefore surface as an unused export.
- **Python `__all__` is not read.** The dead-code layer never consults `__all__`,
  so declaring a public API there does not by itself rescue a symbol. The
  rescues that do apply are the `__init__.py` exemption, the dunder-name skip,
  and intra-module reference tracking.
- **Test-only usage reads as usage.** A test file's import produces a real graph
  edge, so a symbol only its tests touch is not flagged. That is deliberate, but
  it also means repowise will not tell you a symbol is *exclusively* exercised by
  tests. There is no "used only in tests" classification.
- **Recently added code.** A file under 30 days old with no importers is capped
  at `0.55` precisely because it is often unfinished, not dead.
- **Interfaces and abstract bases.** Reached only through implementors, which is
  heuristic detection. Capped, not suppressed.

The evidence list on every finding tells you which of these applied. Read it
before deleting anything.

## CLI reference

| Flag | Description |
|------|-------------|
| `--min-confidence` | Minimum confidence threshold (default `0.5`) |
| `--safe-only` | Only findings marked safe to delete |
| `--kind` | `unreachable_file`, `unused_export`, `unused_internal`, `zombie_package` |
| `--format` | `table` (default), `json`, `md` |
| `--include-internals` / `--no-include-internals` | Private and underscore symbols (default: off) |
| `--include-zombie-packages` / `--no-include-zombie-packages` | Unused declared packages (default: on) |
| `--no-unreachable` | Skip unreachable-file findings |
| `--no-unused-exports` | Skip unused-export findings |
| `--repo` | Workspace mode: target one repo (defaults to primary) |
| `--workspace` / `--no-workspace` | Force workspace or single-repo mode |

Full command reference: [CLI_REFERENCE.md](../reference/CLI_REFERENCE.md#repowise-dead-code-path).

## The `get_dead_code` MCP tool

Findings come back grouped into high, medium, and low tiers, each with the file
path, kind, confidence, line count, and a cleanup impact estimate.

| Parameter | Default | Notes |
|-----------|---------|-------|
| `kind` | all | One of the four finding kinds |
| `min_confidence` | `0.5` | `0.7` and above is cleanup-ready only |
| `tier` | all | `high` (`>= 0.8`), `medium`, `low` |
| `safe_only` | `false` | Deletion-ready only, excluding runtime-load risk |
| `limit` | `20` | Per tier, clamped to 25 |
| `directory` / `owner` | none | Path-prefix and primary-owner filters |
| `group_by` | none | Roll up by `directory` or `owner` instead of a flat list |
| `include_internals` | `false` | Private and underscore symbols |
| `include_zombie_packages` | `true` | |
| `no_unreachable` / `no_unused_exports` | `false` | |
| `repo` | primary | Workspace mode |

This is a tool for cleanup sweeps, not targeted fixes. Turn it off entirely with
`mcp: {tools: ["-get_dead_code"]}` in `.repowise/config.yaml`. Parameter details:
[MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_dead_code).

## Where else it shows up

- The generated `CLAUDE.md` lists dead-code candidates alongside hotspots and
  decisions.
- Contributor profiles carry a dead-code burden per author.
- Module health folds dead-code percentage into its 0-100 composite.
- `repowise update` recomputes findings for changed files only.

## See also

- [INTELLIGENCE_LAYERS.md](INTELLIGENCE_LAYERS.md): how dead code fits the wider index.
- [CODE_HEALTH.md](CODE_HEALTH.md): the scoring layer that shares the same graph and git data.
- [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md): which languages parse into the graph the analysis walks.
