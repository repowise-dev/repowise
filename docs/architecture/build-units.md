# Build units

A build unit is one thing a build tool declares: a .NET project, a Cargo
crate, an npm workspace package, a Gradle project, a Go module, a Maven
module, a Python distribution, a Dart package, a Swift package, a Composer package, a Delphi or Lazarus project
or package. `repowise.core.build_units` reads them once, from the resolver indexes the graph build
already holds, and stores one answer to three questions:

- What units does this repo build?
- Which units own this file?
- Which in-repo units does a unit depend on, and which depend on it?

There is one model, available live on `GraphBuilder.build_units` during a
build and from the store afterwards. Code that needs any of these answers reads
units instead of reading a manifest. See
[single-sources.md](single-sources.md) for the principle.

| File | Role |
|---|---|
| `build_units/model.py` | `BuildUnit` and `BuildUnitIndex` |
| `build_units/collect.py` | `collect_build_units(ctx)`, the one entry point, and the `_ADAPTERS` registry |
| `build_units/adapters/<ecosystem>.py` | One `units(ctx) -> Found` function per ecosystem |
| `build_units/adapters/manifest_roots.py` | The fallback for every package root no adapter claimed |
| `persistence/crud/build_units.py` | `replace_build_units` and `load_build_units` |

Paths are relative to `packages/core/src/repowise/core/`.

## The model

`BuildUnit` is a frozen dataclass:

| Field | Meaning |
|---|---|
| `id` | Ecosystem-prefixed and stable across runs: `dotnet:<manifest>`, `cargo:<crate>`, `npm:<package>`, `gradle:<project path>` (`gradle::` for the root project), `go:<module path>`, `maven:<groupId>:<artifactId>`, `python:<PEP 503 name>`, `swift:<package name>`, `pascal:<project name>` (`pascal:<manifest>` when several projects share the name), `dart:<package name>`, `php:<composer name>` / `php:<dir>` when unnamed, `manifest:<root>` (`manifest:.` at the repo root) |
| `ecosystem` | `dotnet`, `cargo`, `npm`, `gradle`, `go`, `maven`, `python`, `dart`, `swift`, `php`, `pascal` or `manifest` |
| `name` | The name the build tool uses (assembly, crate, package, module path, artifactId) |
| `root` | Repo-relative POSIX directory, `""` at the repo root |
| `manifest` | Repo-relative path of the file that declares the unit |
| `source` | The reader that produced it: `msbuild`, `cargo`, `gradle`, `npm-workspace`, `go-mod`, `maven`, `pyproject`, `pubspec`, `swiftpm`, `composer`, `pascal`, `manifest-root` |
| `deps` | Sorted ids of the in-repo units this one depends on. Third-party dependencies are not units |

`BuildUnitIndex` holds every unit by id and the owners of each file:

| Member | Returns |
|---|---|
| `units` | `{id: BuildUnit}`, sorted by id |
| `file_owners` | `{path: tuple[unit id, ...]}`, sorted |
| `unit(unit_id)` | The unit, or `None` |
| `units_owning(path)` | The units owning `path`, sorted by id. A path the index never saw is answered by the deepest unit root above it |
| `dependents(unit_id)` | The units listing `unit_id` in their `deps` |
| `from_units(units, paths, explicit, failed_ecosystems)` | Builds an index, assigning ownership as described below |
| `failed_ecosystems` | Ecosystems whose reader failed in this collection run. Left out of equality, so an index read back from the store equals the one written |

Both maps are sorted, so two runs over the same tree compare equal and store
the same rows.

## How ownership is decided

1. **Listed files first.** An adapter that knows a unit's exact files lists
   them: a .NET project's compiled sources, a Gradle project's source-set
   directories (which may sit outside the project, `srcDirs("../shared/src")`).
   A listed file is owned by every unit listing it.
2. **Otherwise the deepest unit root.** Any other file is owned by the units
   whose `root` is the deepest directory above it.
3. **Several owners are normal.** Two units at one root (a `Web.csproj` and a
   workspace `package.json` side by side) both own its unlisted files, such as
   `index.ts`. The C# files the .NET project lists are owned by that project
   alone.

Which units exist is decided per manifest:

- Each adapter returns `Found(claims, ruled_out)`. `claims` pairs each unit
  with its listed files. `ruled_out` names manifests the adapter read and
  judged to be no unit: a virtual Cargo workspace, a `package.json` outside
  the declared npm workspace, a POM outside the root reactor.
- A unit's own manifest and its ruled-out manifests are the only ones an
  adapter decides. `manifest_roots` then gives a `manifest:` unit to every
  package root that still holds an undecided manifest.
- **A specific adapter's unit wins** over the fallback at the same root.
- **An unreadable manifest falls back.** A manifest that fails to parse is
  never decided, so its root still gets a `manifest:` unit and its files keep
  an owner.
- **A failed reader costs only its ecosystem.** An adapter that raises is
  logged once, named in `failed_ecosystems`, and its roots are left to the
  fallback. An ecosystem the repo does not use costs one cheap check: each
  adapter first looks for its source files (no `.cs` or `.vb` file means no
  .NET index is built).
- A unit id produced twice keeps the first by root, and `deps` naming a unit
  the collection did not produce are dropped.

## Ecosystems

| Ecosystem | Reader it builds on | Gives | Deps from |
|---|---|---|---|
| .NET | `DotNetProjectIndex` (`ingestion/resolvers/dotnet`) | One unit per project, with its compiled files listed | `<ProjectReference>` |
| Cargo | `CargoWorkspaceIndex` (`ingestion/resolvers/rust_workspace.py`), plus the package-root scan for crates outside the workspace | One unit per crate, in or out of the root workspace | `path` dependencies, `{ workspace = true }` resolved through the root's `[workspace.dependencies]` |
| Gradle | `JvmGradleIndex` (`ingestion/resolvers/jvm_gradle.py`) | One unit per project, the root project included when it has a build script, also in a multi-project build; files listed from source sets | `project(":x")` arguments of configuration calls in the build script, string-named configurations included |
| npm, yarn, pnpm | The TypeScript resolver's workspace reader (`ingestion/resolvers/ts_workspace`) | One unit per workspace member; non-member `package.json` files are ruled out | Member names in `dependencies`, `devDependencies`, `peerDependencies`, `optionalDependencies` |
| Go | `GoPackageIndex` (`ingestion/resolvers/go_workspace.py`) and `scan_go_mod` | One unit per module (`go.mod`), not per package | `require` entries naming another module of the repo |
| Maven | `load_maven_reactor` (`ingestion/external_systems/maven_model.py`) | One unit per module of the root `pom.xml` reactor, followed recursively, with coordinates inherited from local parents | `<dependencies>` naming another module's `groupId:artifactId`, inherited and at every scope |
| PHP | `repo_composer_manifests` in `ingestion/composer.py` | One unit per first-party `composer.json` | `require`/`require-dev` names that live at one of the manifest's path repositories, globs included |
| Python | `pyproject.toml` read with `read_toml_or_none`, names normalised as PyPI does | One unit per `pyproject.toml` naming a distribution (`[project] name`, else `[tool.poetry] name`) | `[project]` dependencies and optional dependencies, Poetry dependencies, uv `workspace = true` sources, matched by name; uv and Poetry `path` sources matched by the directory they point at |
| Dart | `pubspec.yaml` read with `yaml.safe_load`, name from top-level `name` | One unit per `pubspec.yaml` naming a package (`name:`) | `path:` dependencies in `dependencies` and `dev_dependencies`, resolved against the package root and matched by directory |
| Swift | `Package.swift` read with `parse_package_manifest` | One unit per `Package.swift` naming a package (`Package(name: "...")`) | `.package(path: "...")` calls matched by the directory they point at |
| Delphi, Lazarus | `PascalProjectIndex` (`ingestion/resolvers/pascal.py`) | One unit per readable `.dpr`, `.dpk`, `.lpr` and `.lpk`, listing the files its `uses` and `contains` clauses reach | `.dpk` `requires`, `.lpk` `RequiredPkgs`, `.lpi` `RequiredPackages`, and `.dproj` `DCC_UsePackage` under `UsePackages`, matched by name to packages of the same toolchain, the requiring project's directory first |
| Fallback | `scan_package_manifests` over the language registry's package manifest names | One `manifest:` unit per undecided package root (Ruby and the rest) | None |

### Ceilings

Each adapter's module docstring states its limits. The ones that change an
answer:

- **Gradle edges from any build mode are kept.** A `project()` dependency
  inside a property-gated branch (`when (testMode) { KSP -> ... }`) counts: a
  static reader does not evaluate properties, and the edge is real in the
  mode that declares it. Type-safe accessors (`projects.core`) and
  dependencies added by convention plugins are not read; reading Gradle's own
  model export is the upgrade path.
- **Maven needs resolved coordinates.** A POM whose coordinates keep an
  unresolved `${...}`, a POM that does not parse, and any repo without a root
  `pom.xml` fall back to `manifest:` units.
- **Python names come from `pyproject.toml`.** A `setup.py` or `setup.cfg`
  project, a pip application whose directory holds only `requirements*.txt`,
  and a `pyproject.toml` naming no distribution, fall back. PEP 735
  `[dependency-groups]` are seen only through their uv source.
- **Cargo crates outside the root workspace** are units with the path
  dependencies their own manifest declares.
- **Dart path deps are matched by directory.** A `path:` dependency is resolved
  against the package root and matched by the directory it points at, so a
  hosted or git dependency named like an in-repo package links nothing; the
  upgrade path is reading `pubspec.lock` / `package_config.json`.
- **Swift is a regex reader.** It does not evaluate `#if` conditions,
  computed values, or variables holding paths; targets are not units;
  `.package(url: ...)` and registry deps (`.package(id: ...)`) are external;
  a local package reached by `file://` is not linked. Upgrade path:
  `swift package dump-package` / `Package.resolved`.
- **PHP only reads path repositories.** A `composer.json` requiring a package
  by name without a `{"type": "path"}` repository pointing at it links nothing;
  reading `composer show --tree` / `composer.lock` is the upgrade path.
- **Pascal files follow `uses`.** A build script's `dcc -U` paths are not
  known, so a name the project's paths do not place takes the same-named file
  in a directory the project already reaches. When that does not single one
  out, every candidate is listed, and a project built for several platforms
  lists each platform's files. Conditional defines
  and `.dproj` build configurations are not evaluated, so a unit or package
  named under any of them counts. Pascal declares no package manifest, so an
  unreadable project gets no `manifest:` unit: its files go to the deepest
  other unit.
- **The fallback has no deps.** Ecosystems without an adapter get correct
  roots and ownership but no edges until an adapter is added.

## Storage

Three tables, created in migration `0129_build_units` and by `init_db` for
local stores:

| Table | Key | Holds |
|---|---|---|
| `build_units` | `(repository_id, unit_id)` | `ecosystem`, `name`, `root`, `manifest`, `source` |
| `build_unit_deps` | `(repository_id, unit_id, dep_unit_id)` | One row per in-repo dependency |
| `build_unit_files` | `(repository_id, path, unit_id)`, indexed on `(repository_id, unit_id)` | One row per file and owning unit |

- **When they are written.** `GraphBuilder.build()` calls
  `collect_build_units` right after the resolver warmups and keeps the result
  on `GraphBuilder.build_units`. `persist_build_units` (`pipeline/persist/ingestion.py`) then
  writes it on `init`, and the incremental `Build units persist` step writes
  it on `update`. A builder that collected nothing (a rehydrated graph, a
  collection that raised) writes nothing, so the stored units stay those of
  the last build that read them.
- **Replaced whole.** `replace_build_units` deletes the repository's rows in
  each table and bulk-inserts the new index, so a deleted file or a removed
  workspace member leaves no row and no separate prune is needed.
- **A failed reader keeps its rows.** For each ecosystem in
  `failed_ecosystems`, the stored units of that ecosystem are read back and
  merged in place of whatever the fallback produced at their roots. Kept file
  rows survive only for paths the fresh index still owns.
- **Reading back.** `load_build_units(session, repo_id)` returns the same
  `BuildUnitIndex` the build collected, or an empty index when no rows are
  stored. `pipeline/upgrade.py` passes it to
  `GraphBuilder.from_persisted(..., build_units=...)`, so a graph rehydrated
  from SQL carries its units without re-reading a manifest. It passes `None`
  for an empty index, so a store with no rows reads as "unknown", not as "no
  units".

## How precision is measured

Each ecosystem is checked against its build tool's own answer:

| Ecosystem | Oracle |
|---|---|
| Cargo | `cargo metadata --no-deps` for the root workspace and every other tracked `Cargo.toml`; edges are `path` dependencies |
| npm | `pnpm ls -r --json` members; edges are `link:` versions in `pnpm-lock.yaml` |
| Go | `go mod edit -json` per tracked `go.mod`; edges are in-repo `require` entries |
| Python | `uv.lock` editable and virtual packages; edges are their dependencies naming a member |
| Maven | `mvn help:effective-pom` over the reactor |
| .NET | `dotnet sln list` and `dotnet list <project> reference` |
| Gradle | An init script at `projectsEvaluated` listing every project and each configuration's declared project dependencies, unioned across the repo's build modes |

Measured against eight public repos (BurntSushi/ripgrep, changesets/changesets,
open-telemetry/opentelemetry-go, modelcontextprotocol/python-sdk,
spring-petclinic/spring-petclinic-microservices, apache/commons-vfs,
jasontaylordev/CleanArchitecture and square/moshi), unit precision and recall
and edge precision and recall are 1.0 on every repo. File ownership agrees
with the oracle's deepest unit on 50 of 50 sampled source files per repo. No
adapter fails on any of them.

## Adding an adapter

Open adapter requests: Ruby
([#5802](https://github.com/repowise-dev/repowise/issues/5802)), Elixir
([#5806](https://github.com/repowise-dev/repowise/issues/5806)) and Scala
([#5807](https://github.com/repowise-dev/repowise/issues/5807)). C and C++
targets (CMake, Bazel) are tracked with the build-graph partitioner in
[#5775](https://github.com/repowise-dev/repowise/issues/5775).

1. **Check the manifest names.** The ecosystem's package manifests must be in
   its `LanguageSpec.manifest_files` (or `manifest_globs`), and files that only
   configure a build in `build_config_manifests`. The fallback and the
   package-root scan read these; see
   [language-support.md](language-support.md#adding-a-new-language).
2. **Write one module**, `build_units/adapters/<ecosystem>.py`, exposing
   `units(ctx: ResolverContext) -> Found`. `cargo.py` and `python.py` are good
   templates.
3. **Return early when the ecosystem is absent.** Check `ctx.path_set` for the
   ecosystem's source files or `package_manifests(ctx)` for its manifest
   before building anything.
4. **Reuse the ecosystem's existing reader.** If a resolver or
   `external_systems` module already parses the manifest, read it through its
   cached `get_or_build_*` accessor on `ctx`. Never parse a manifest a second
   way. Use `package_manifests(ctx)` from `manifest_roots.py` for the roots on
   disk, `read_toml_or_none` for TOML, and `fs_walk.walk_repo` if you must
   walk.
5. **Build units with stable ids** (`<ecosystem>:<name the tool uses>`),
   repo-relative POSIX `root` and `manifest`, and `deps` as ids of other units
   of the same ecosystem. Unknown ids in `deps` are dropped for you.
6. **Decide manifests explicitly.** Claim each unit with its listed files (or
   `()` to own by root). Put manifests you read and judged to be no unit in
   `ruled_out`. Leave a manifest you could not read out of both, so the
   fallback keeps its root.
7. **Register it** in `_ADAPTERS` in `build_units/collect.py`, with the
   ecosystem name used in `BuildUnit.ecosystem`.
8. **Add fixture tests** to `tests/unit/build_units/test_collect.py`, one
   small on-disk repo per case: units and deps, a path dependency pointing
   outside the repo, an unreadable manifest falling back, a repo without the
   ecosystem never building its index, and the adapter beating the fallback
   at its root.
9. **State the ceiling** in the module docstring: what the static reader does
   not see, and the upgrade path.
