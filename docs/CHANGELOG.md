# Changelog

All notable changes to repowise will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- Use `git-cliff` to auto-generate entries from conventional commits -->

---

## [0.4.0] — Unreleased

### Added

#### C# Full tier
- **MSBuild-aware import resolver** — new `resolvers/dotnet/` subpackage parses every `.csproj` and `.sln` in the repo, builds a namespace → file map across projects, walks `Directory.Build.props` ancestry, and resolves `using` directives by ranking candidates: same project → directly-referenced project → anywhere. NuGet `<PackageReference>` ids are emitted as `external:nuget:<id>` nodes. Falls back to legacy stem-match for repos without `.csproj`.
- **Modern C# language features** — `csharp.scm` now captures `record_declaration`, `delegate_declaration`, `event_declaration`/`event_field_declaration`, `field_declaration`, `enum_member_declaration`, and both block-form and file-scoped `namespace_declaration`. `LANGUAGE_CONFIGS` and the registry's `heritage_node_types` are extended accordingly.
- **`global using` / `using static` / `using alias` propagation** — `NamedBinding` gains `is_global` and `is_static_import` flags; `extract_csharp_bindings` distinguishes all four flavours of `using` directive. Default `<ImplicitUsings>` set (with Web SDK extras) and `global using` lines are merged into a per-project implicit-usings set used by the resolver.
- **XML doc parsing** — module-level and symbol-level `///` runs are extracted, `<summary>` content is unwrapped as the rendered docstring, structural tags (`<param>`, `<returns>`, `<see/>`) are stripped, and `<inheritdoc/>` emits a `{inheritdoc}` marker.
- **Heritage for records** — `record User(...) : Base(args), IInterface` now produces both `extends` and `implements` edges; primary-constructor argument lists are skipped.
- **ASP.NET / .NET framework edges** — `_add_aspnet_edges()` runs whenever the tech stack mentions ASP.NET or any `.cs` file imports `Microsoft.AspNetCore.*`. Adds edges from `Program.cs` / `Startup.cs` to every `[ApiController]` file, `app.MapGet/...` handler classes, `app.UseMiddleware<T>()` middleware, and from each `DbContext` to entity files referenced via `DbSet<T>`.
- **.NET dynamic hints** — new `DotNetDynamicHints` extractor (registered in `HintRegistry`) records DI registrations (`AddScoped`/`AddSingleton`/`AddTransient`/`AddHostedService`), reflection (`Activator.CreateInstance`, `Type.GetType`, `Assembly.Load*`), `[assembly: InternalsVisibleTo]`, and MEF `[Export]`/`[ImportMany]` as graph edges.
- **Workspace contract extraction for ASP.NET and gRPC-dotnet** — `http_extractor.py` learns `[HttpGet/Post/...]` attribute routing with class-level `[Route]` prefix stitching, parameterless `[HttpVerb]` attributes, minimal API (`app.MapGet`/...), and HttpClient consumers (`*Async`). `grpc_extractor.py` recognises `app.MapGrpcService<T>()`, `class X : Service.ServiceBase`, and `new ServiceClient(channel)`.
- **Cross-repo `<ProjectReference>` and internal NuGet** — `cross_repo._scan_csproj` walks every `.csproj` in every workspace repo and emits `dotnet_project_ref` for cross-repo project references and `dotnet_nuget_internal` when a `<PackageReference>` id matches a sibling repo's `<AssemblyName>`.
- **Dead-code dynamic markers for C#** — `_DYNAMIC_IMPORT_MARKERS` learns reflection / DI / MEF / `InternalsVisibleTo` patterns so the dead-code analyser doesn't flag types only loaded by the framework at runtime.
- **Multi-project test fixtures** — `tests/fixtures/dotnet_solution/` (Api / Domain / Infrastructure with EF Core, controllers, minimal API, GlobalUsings) and `tests/fixtures/dotnet_workspace/` (3 repos demonstrating cross-repo `<ProjectReference>` + internal-NuGet patterns), with end-to-end coverage in `tests/integration/test_dotnet_solution.py`.

#### Dead-code accuracy
- **Dynamic-edge consumption in dead-code analysis** — graph edges of type `dynamic` / `dynamic_*` (emitted by every dynamic-hint extractor) now suppress dead-code findings automatically. `find_dynamic_edge_files()` enumerates files involved in those edges and unions the result with the existing source-text `_DYNAMIC_IMPORT_MARKERS` scan. Sub-types (`dynamic_uses`, `dynamic_imports`) are preserved on the graph edge instead of being squashed.
- **Per-language dynamic-import markers** — `_DYNAMIC_IMPORT_MARKERS` extends to Go (`reflect.TypeOf`/`reflect.ValueOf`), Ruby (`Object.send`, `Kernel.const_get`, `.public_send`), PHP (`call_user_func*`, `new $class`, `ReflectionClass`), Kotlin (`KClass.createInstance`, `::class.java`), Swift (`NSClassFromString`, `Selector`, `#selector`, `NSStringFromClass`), and Scala (`Class.forName`, `runtimeMirror`, `reflect.runtime`).
- **`detect_unused_internals` enabled by default** — private-symbol findings now surface in the standard dead-code report at confidence 0.65 with `safe_to_delete=False`. CLI defaults stay explicit-False so `repowise dead-code` is unchanged unless `--include-internals` is passed.

#### Workspace-aware resolvers across the Good tier
- **PHP composer PSR-4** — `resolvers/php_composer.py` reads `autoload.psr-4` and `autoload-dev.psr-4` from `composer.json`, builds a longest-prefix-wins namespace → directory map, and is consulted before stem fallback. Real Laravel/Symfony apps with `"App\\": "src/"` style maps now resolve.
- **Go multi-module monorepos** — `resolve_go_import` walks every `go.mod` in the repo (skipping `vendor`/`node_modules`), records `(module_dir, module_path)` tuples on the resolver context, and matches imports by longest module prefix. Single-module back-compat preserved.
- **TypeScript SFC + workspace package resolution** — `.vue`, `.svelte`, and `.astro` extensions probed only when the repo actually contains SFC files. npm/yarn/pnpm `workspaces` (array or object form, with glob expansion) are parsed from root `package.json` so `@scope/pkg` and `@scope/pkg/sub/path` resolve to the sibling workspace dir before falling back to `external:`.
- **Kotlin Gradle subprojects** — `resolvers/kotlin_gradle.py` parses `settings.gradle(.kts)` `include(...)` declarations plus per-module `srcDirs(...)` overrides (defaults `src/main/kotlin`, `src/main/java`), then walks each source root recording `package` declarations into a `package_to_files` map.
- **Ruby Rails / Zeitwerk autoloading** — gated on `config/application.rb`, `resolvers/ruby_rails.py` builds bare-name and namespaced-name maps over standard autoload roots (`app/*`, `lib/`). `ResolverContext.rails_lookup` exposes the index for callers (heritage, call resolution, framework edges).
- **Swift SPM target → directory mapping** — `resolvers/swift_spm.py` regex-parses `.target(name: "X", path: "Y")`, `.executableTarget`, and `.testTarget` declarations across all `Package.swift` files in the repo (defaults `Sources/<Name>` for code, `Tests/<Name>` for tests).
- **Scala SBT / Mill multi-project** — `resolvers/scala_build.py` autodetects the build tool (`build.sbt` vs `build.sc`) and parses subprojects (SBT `lazy val core = project.in(file("core"))`, Mill `object Foo extends ScalaModule`). Walks each project's `src/main/scala` (or `src/`) recording packages into `package_to_files`.
- **Cargo workspace crate resolution** — `resolvers/rust_workspace.py` parses root `Cargo.toml` `[workspace] members = [...]` plus each member's `[package] name`. `resolve_rust_import` consults the index after the same-crate probe so `use sibling_crate::module` resolves to the sibling crate's `src/`. Cargo's `-` → `_` import-identifier rewrite is honoured.

#### Framework-aware edges (every major web framework)
- **Spring Boot (Java/Kotlin)** — `@Component`/`@Service`/`@Repository`/`@Controller`/`@RestController`/`@Configuration` bean classes wire to their injection sites via `@Autowired` field/constructor analysis. Interface-typed dependencies fall back to `parsed.heritage` to find implementing classes. `@Bean` factory methods in `@Configuration` classes link to their return-type files.
- **Rails (Ruby)** — `config/routes.rb` is line-walked with namespace-stack tracking: `resources :users`, `get "/foo", to: "users#index"`, and nested `namespace :admin do … end` all resolve to controller files via the Zeitwerk autoload index. ActiveRecord `belongs_to`/`has_many`/`has_one` relationships link model files (with simple inflector-style singularisation).
- **Laravel (PHP)** — `routes/web.php` and `routes/api.php` parse modern `[Foo::class, 'method']` and legacy `'Foo@method'` syntaxes, plus `Route::resource`. Service-provider `bind`/`singleton`/`instance` calls link providers to bound classes. Eloquent `hasMany`/`belongsTo`/`hasOne` link models. Class resolution uses the composer PSR-4 map first, falling back to stem.
- **Express / NestJS (TS/JS)** — Express `app.use(routerVar)` mirrors the FastAPI router-var pattern (resolves imported names ending in `Router`/`router` to source file). NestJS `@Module({ controllers: [...], providers: [...], imports: [...] })` arrays parse into module → target edges using a class-name → file map.
- **Gin / Echo / Chi (Go)** — `r.GET("/p", users.Index)` style handler references resolve via the Go import list (using the multi-module resolver) for package-qualified handlers, or via a function-name → file map for receiver methods. Lambda handlers are accepted as missed.
- **Axum / Actix (Rust)** — Axum `Router::new().route("/p", get(handler))`, Actix `web::resource("/p").route(web::get().to(handler))` / `.service(handler)` / `.configure(routes::register)` all resolve to handler files via a function-name → file map.

#### Per-language dynamic-hint extractors
- **Spring (JVM)** — `applicationContext.getBean(Foo.class)` and named-bean lookups, plus `@Bean` factory return-types.
- **Ruby** — `Object.send(:method)` / `.public_send`, `Kernel.const_get`, `define_method`, ActiveSupport `delegate :foo, to: :bar`.
- **PHP** — `call_user_func`/`call_user_func_array`, `new ReflectionClass(Foo::class)`, container `get`/`app`/`resolve`/`make` with `::class` arguments, `new $variable` instantiation markers.
- **Scala** — `Class.forName(...)`, `runtimeMirror` / `reflect.runtime` markers, named `given foo: Bar = ???` and `implicit val foo: Bar = ???` declarations.
- **Swift** — `NSClassFromString("Foo")`, `NSStringFromClass(Foo)`, `Selector("name")`, `#selector(name)`, KVC `value(forKey: "key")`.
- **C** — function-pointer assignment (`fp = some_function;` where the right-hand side is a known function name), `dlopen("./libfoo.so")`, `dlsym(handle, "name")`.
- **Luau** — `game:GetService("Name")`, `setmetatable(t, {__index = Other})`, `require(game.Service.Path)` markers.
- **Go** — `reflect.TypeOf(Foo{})`, `plugin.Open(...)`, `plugin.Lookup(...)`.

#### Symbol-extraction coverage
- **Java records** — `record Point(double x, double y) {}` now captured as a class-kind symbol with optional modifiers.
- **Kotlin** — `typealias Foo = Bar` and top-level / class-level `val`/`var` properties (locals inside function bodies remain excluded).
- **Scala 3** — `enum_definition`, `given_definition` (named givens), and `var_definition` are now captured. `class_definition` and `function_definition` also capture leading annotations (`@deprecated`, `@tailrec`).
- **Swift** — `subscript_declaration` captured as a method-kind symbol.
- **Ruby** — top-level / class-level constant assignments (`MAX_RETRIES = 3`).
- **PHP** — `const_declaration` and `property_declaration` (with or without explicit visibility) at both file and class scope.
- **C** — `typedef int MyInt;` and `typedef struct Foo Bar;` aliases now produce symbols.
- **Java class/interface/record annotations** — `(modifiers) @symbol.modifiers` capture extended to `class_declaration`, `interface_declaration`, and `record_declaration` so framework decorators surface in the symbol view.

#### Documentation extraction
- **Java module-level Javadoc** — `extract_module_docstring` gains a Java branch that picks up a leading `/** ... */` block before the package/import declarations.
- **Luau docstrings** — both `--[[ block comment ]]` and runs of `---` triple-dash lines are extracted at module and symbol scope.

### Fixed
- **Java interface inheritance** — `interface IFoo extends IBase` now produces a heritage relation; the extractor previously only recognised the `interfaces` field on `class_declaration` and missed `extends_interfaces` on `interface_declaration`.
- **Go struct embedding** — `type Foo struct { Base }` correctly emits a heritage edge from `Foo` to `Base`. The Go heritage extractor now traverses the `field_declaration_list` child when no `body` field is present (matches the actual tree-sitter-go grammar layout).
- **Swift `extension_declaration` heritage** — extension conformance declarations now contribute heritage relations (`extension_declaration` was missing from Swift's `heritage_node_types`).

### Changed
- **Language tier promotion** — C# moves from "Good" to "Full" in `README.md` and `docs/LANGUAGE_SUPPORT.md`. Eight languages now sit at Full tier (was: seven).
- **Heritage / bindings / dead-code internals refactored into per-language subpackages** — `extractors/heritage.py` and `extractors/bindings.py` (previously 600+ LOC monoliths) and `analysis/dead_code.py` are now subpackages with one file per language plus a re-export shim. Public API (`extract_heritage`, `extract_import_bindings`, `DeadCodeAnalyzer`, etc.) is unchanged.

### Tests
- **+90 unit tests** covering workspace-aware resolvers (PHP, Go, TypeScript, Swift, Kotlin, Scala, Ruby, Rust), framework-edge extraction (Spring, Rails, Laravel, Express/NestJS, Gin/Echo/Chi, Axum/Actix), per-language dynamic-hint extractors, and Java/Ruby/Scala/PHP/Go heritage + binding extractors.

---

## [0.3.1] — 2026-04-26

### Added
- **Output language for generated wiki content** (#99) — set `language: ru` (or any of `en`, `es`, `fr`, `de`, `zh`, `ja`, `ko`, `it`, `pt`, `nl`, `pl`, `tr`, `ar`, `hi`) in `.repowise/config.yaml` to have the LLM produce documentation in that language. Code, paths, and symbol names stay untranslated. Cache keys include the language so different output languages do not collide. Closes #64.
- **Luau / Roblox language support** (#89) — promotes the existing git-blame-only `lua` LanguageSpec to a full AST-parsed `luau` tier covering both `.lua` and `.luau`. Includes a dedicated resolver for string-literal `require` plus `script.Parent` instance paths and the `:WaitForChild` / `:FindFirstChild` Rojo-safe idioms. Closes #52.
- **OpenRouter provider** (#56) — new `openrouter` LLM provider with full `stream_chat` plus tool-call support, plus an `OpenRouterEmbedder` defaulting to `google/gemini-embedding-001`. Sends OpenRouter's recommended `HTTP-Referer` and `X-Title` headers.
- **`base_url` plus per-provider env vars** (#85) — OpenAI, Anthropic, Gemini, Ollama, and LiteLLM all accept a `base_url` (with `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`, `GEMINI_BASE_URL`, `OLLAMA_BASE_URL`, `LITELLM_BASE_URL` env fallbacks) so users can route requests through proxies and self-hosted OpenAI-compatible endpoints.

### Fixed
- **`database is locked` on concurrent `repowise update`** (#101) — every SQLite connection now opens with `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, and `foreign_keys=ON`. Two concurrent writers against the same workspace no longer collide; PostgreSQL is unchanged. Closes #95.
- **CLAUDE.md opt-out ignored in full mode** (#102) — the "Generate .claude/CLAUDE.md? [Y/n]" prompt was nested inside the advanced-config flow, so users in full mode were never asked and the writer always created the file. Prompt is now extracted into a standalone helper and asked in both modes. Closes #81.
- **`repowise init` could overwrite an unparseable user JSON config** (#94) — when `.mcp.json` or `.claude/settings.json` exists but is not valid JSON, init now aborts with a clear error instead of silently treating the file as empty and overwriting the user's contents.
- **Editable installs and CI builds were broken** (#97) — `[tool.setuptools].packages` referenced `repowise.core.ingestion.parsers` (no longer exists) and was missing `extractors`, `languages`, and `resolvers` (added during the language-support refactor). Resyncing the list unblocks `pip install -e .` and every PR's CI.
- **`repowise serve` pointed at the wrong GitHub release** — `_GITHUB_REPO` flipped from `RaghavChamadiya/repowise` to `repowise-dev/repowise` so the web UI tarball downloads from the correct release URL. Project URLs on PyPI updated to match.

### Changed
- **PreToolUse hook** — replaced FTS-only file retrieval with multi-signal ranking: symbol name match (highest weight), file path match, then FTS on wiki content. Returns top 3 files instead of 5. Removed git signals (HOTSPOT, bus-factor, owner) from enrichment output — use `get_risk` for that. Removed Bash command interception. Dependencies shown as "Uses" (2 per file) alongside symbols (3) and importers (3).
- **uv workflow documented and dev deps migrated to PEP 735** (#100) — README and USER_GUIDE document `uv tool install repowise` and `uv sync --all-packages`. Replaces the deprecated `[tool.uv] dev-dependencies` table with `[dependency-groups] dev`, silencing the `tool.uv.dev-dependencies is deprecated` warning every `uv pip install` was emitting.

### Security
- Bumps `dompurify` 3.3.3 → 3.4.1 (prototype-pollution + mXSS sanitizer-bypass fixes).
- Bumps `gitpython` 3.1.46 → 3.1.47 (argument injection via underscored kwargs).
- Bumps `mako` 1.3.10 → 1.3.11 (`TemplateLookup` path traversal).
- Bumps `litellm` 1.83.0 → 1.83.7 (routine patches).
- Bumps `python-multipart` 0.0.22 → 0.0.26 (case-insensitive headers, MIME info).

---

## [0.3.0] — 2026-04-13

### Added

#### Multi-repo workspaces
- **Workspace support** — `repowise init .` from a parent directory scans for git repos (up to 3 levels deep), prompts for selection, and indexes each repo with cross-repo analysis. Config stored in `.repowise-workspace.yaml`.
- **Workspace CLI commands** — `repowise workspace list`, `workspace add <path>`, `workspace remove <alias>`, `workspace scan`, `workspace set-default <alias>` for managing repos in a workspace.
- **Workspace-aware MCP server** — a single MCP server instance serves all workspace repos. Tools accept an optional `repo` parameter to target a specific repo or `"all"` to query across the workspace. Lazy-loading with LRU eviction (max 5 repos loaded simultaneously).
- **Cross-repo co-change detection** — analyzes git history across repos to find files that frequently change in the same time window.
- **API contract extraction** — scans for HTTP route handlers (Express, FastAPI, Spring, Go), gRPC service definitions, and message topic publishers/subscribers. Matches providers with consumers across repos.
- **Package dependency scanning** — reads package manifests (`package.json`, `pyproject.toml`, `go.mod`, `pom.xml`) to detect inter-repo package dependencies.
- **Workspace CLAUDE.md** — auto-generated context file at the workspace root covering all repos, their relationships, cross-repo signals, and contract links.
- **Workspace web UI** — workspace dashboard (`/workspace`) with aggregate stats and repo cards, contracts view (`/workspace/contracts`) with provider/consumer matching, and co-changes view (`/workspace/co-changes`) with cross-repo file pairs ranked by strength.
- **Workspace update** — `repowise update --workspace` updates all stale repos in parallel (up to 4 concurrent) then re-runs cross-repo analysis. `--repo <alias>` targets a single repo.
- **Workspace watch** — `repowise watch --workspace` auto-updates all workspace repos on file change.

#### Auto-sync hooks
- **`repowise hook` CLI** — `repowise hook install` installs a marker-delimited post-commit git hook that runs `repowise update` in the background after every commit. `hook install --workspace` installs for all workspace repos. `hook status` and `hook uninstall` for management.
- **Proactive context enrichment via Claude Code hooks** — `repowise init` registers PreToolUse and PostToolUse hooks in `~/.claude/settings.json`. PreToolUse enriches every `Grep`/`Glob` call with graph context (importers, dependencies, symbols, git signals) at ~24ms latency. PostToolUse detects git commits and notifies the agent when the wiki is stale.
- **Polling scheduler** — when the server is running, a background job polls registered repositories every 15 minutes and triggers updates for new commits missed by webhooks.

#### Graph intelligence
- **Symbol-level dependency graph** — the dependency graph is now two-tier: file nodes for module-level relationships and symbol nodes (functions, classes, methods) for fine-grained call resolution. Call edges carry confidence scores (0.0–1.0).
- **3-tier call resolution** — Tier 1: same-file targets (confidence 0.95). Tier 2: import-scoped targets via named bindings (0.85–0.93). Tier 3: global unique match (0.50). Extracted by tree-sitter for Python, TypeScript, JavaScript, Go, Rust, Java, and C++.
- **Named binding resolution** — tracks import aliases, barrel re-exports (`__init__.py`, `index.ts`), and namespace imports across all 7 full-tier languages.
- **Heritage extraction** — class inheritance and interface implementation for 11 languages (Python, TypeScript, JavaScript, Java, Go, Rust, C++, Kotlin, Ruby, C#, C) with `extends`/`implements` graph edges.
- **Leiden community detection** — two-level community detection (file communities from import edges, symbol communities from call/heritage edges) with cohesion scoring and heuristic labeling. Falls back to NetworkX Louvain when graspologic is unavailable.
- **Execution flow tracing** — 5-signal entry point scoring (fan-out ratio, in-degree, visibility, name pattern, framework hint) with BFS call-path discovery and cross-community classification.
- **Graph query indexes** (migration `0017`) — composite indexes for sub-millisecond graph queries.

#### Web UI
- **Graph Intelligence on Overview** — expandable community list (labels, cohesion, member counts) and execution flows panel with call-path traces on the overview dashboard.
- **Wiki sidebar** — new collapsible section showing PageRank and betweenness percentile bars, community label, and in/out degree for the current file.
- **Symbols drawer** — right panel with graph metrics, callers/callees (with confidence scores), and heritage (extends/implements) for class nodes.
- **Graph page** — community color mode uses real community labels from Leiden detection. Clicking a node opens a community detail panel. Active color mode preserved as a URL parameter.
- **Contributor network, hotspot, and ownership pages** — new dedicated pages for git intelligence.
- **Docs viewer** — enriched with graph intelligence sidebar, version history, and improved markdown rendering.
- **5 new graph REST API endpoints** — communities list, community detail, node metrics, callers/callees, and execution flows.

#### Other
- **Improved init UX** — pre-scan phase shows repo size and language breakdown before confirming. Advanced config options grouped logically with live insights during indexing.
- **Doc generation enriched with graph intelligence** — wiki page generation prompts now include community context, caller/callee information, and heritage relationships.

### Changed
- **`get_overview`** now includes `community_summary` — top communities by size with labels and cohesion scores.
- **`get_context`** now includes `community` block per file target with community ID and label (when `compact=False`). In workspace mode, enriched with cross-repo co-change and contract data.
- **`get_risk`** enriched with cross-repo signals in workspace mode — co-change partners from other repos and contract dependencies.
- **`search_codebase`** in workspace mode searches across all repos and merges results.
- **Job executor** — improved progress tracking, concurrent run detection (HTTP 409), and crash recovery for stale running jobs on server startup.

---

## [0.2.3] — 2026-04-11

### Added
- **`annotate_file` MCP tool** — attach human-authored notes to any wiki page. Notes survive LLM-driven re-generation and appear in `get_context` responses and the web UI. Pass an empty string to clear notes.
- **`repowise export --full`** — full JSON export now includes decision records, dead code findings, git hotspots, and per-page provenance metadata (confidence, freshness, model, provider).
- **Rust import resolution** — `use crate::`, `super::`, and `self::` imports now resolve to local files via crate root detection (`lib.rs`/`main.rs`). External crates mapped to `external:` nodes.
- **Go import resolution** — `go.mod` module path parsing enables accurate local vs external package classification. Local imports resolve by suffix matching against the module path.
- **C/C++ parser improvements** — added captures for `template_declaration`, `type_definition` (typedef struct/enum), `preproc_def` (#define), `preproc_function_def`, and forward declarations.
- **Go parser** — added `const_spec` and `var_spec` captures for package-level constants and variables.
- **Rust parser** — added `macro_definition` capture for `macro_rules!` macros.
- **Dynamic import detection** — dead code analysis now scans for `importlib.import_module()` and `__import__()` calls; files in the same package receive reduced confidence (capped at 0.4).
- **Framework decorator awareness** — Flask, FastAPI, and Django route/endpoint decorators added to `_FRAMEWORK_DECORATORS`. Decorated functions are never flagged as dead code.
- **`human_notes` column on wiki pages** — persists across re-indexing. Alembic migration `0014_page_human_notes`.
- **Decision staleness scoring during ingestion** — `compute_staleness()` now runs during `repowise init`, not just `repowise update`.

### Changed
- **CLAUDE.md template** — replaced imperative "MUST use" / "CRITICAL" language with advisory framing. Added `indexed_commit` display. Made `update_decision_records` optional ("SHOULD for architectural changes").
- **`get_context` freshness** — freshness data now included by default instead of requiring explicit `include=["freshness"]`.
- **`get_answer` docstring** — removed "do NOT verify by Read" instruction. High-confidence note changed to "verify cited file paths exist before acting on them".
- **Token budget caps** — `get_overview` caps knowledge_silos (30), module_pages (20), entry_points (15). `get_why` caps file_commits (10).
- **Dead code patterns** — expanded `_DEFAULT_DYNAMIC_PATTERNS` with `*Mixin`, `*Command`, `*_view`, `*_endpoint`, `*_route`, `*_callback`, `*_signal`, `*_task`.

### Docs
- **README** — tool count updated to 11, `annotate_file` added to MCP tools table, `--full` export flag documented, dynamic import detection noted, comparison table updated.
- **Supported languages** — tiered table with accurate "What works" descriptions per language.
- Updated USER_GUIDE.md, ARCHITECTURE.md, and deep-dives.md to reflect all changes.

---

## [0.2.2] — 2026-04-11

### Added
- **tsconfig/jsconfig path alias resolution** (#40) — new `TsconfigResolver` discovers all `tsconfig.json` / `jsconfig.json` files, resolves `extends` chains (with circular detection), and maps path aliases (e.g. `@/*` -> `src/*`) to real files during graph construction. Non-relative TS/JS imports that match a path alias now create proper internal edges instead of phantom `external:` nodes. Fixes broken dependency graph, PageRank, dead code false positives, and change propagation for any TS/JS project using path aliases (Next.js, Vite, Angular, Nuxt, CRA).
- **Traversal stats** (#57) — `FileTraverser` now tracks skip reasons (`.gitignore`, blocked extension, binary, oversized, generated, `--exclude`, `.repowiseIgnore`, unknown language) via a new `TraversalStats` dataclass. Stats are surfaced after traversal as a filtering summary showing how many files were included vs excluded and why.
- **Submodule handling** (#57) — git submodule directories (parsed from `.gitmodules`) are now excluded by default during traversal. Added `--include-submodules` flag to `repowise init` to opt in.
- **Language breakdown** (#57) — generation plan table now shows language distribution (e.g. "Languages: python 79%, typescript 14%"). Completion panel shows top languages with percentages instead of just a count.
- **Multi-line exclude input** — interactive advanced mode now prompts for exclude patterns one per line instead of comma-separated on a single line.
- 38 new unit tests covering tsconfig resolver, traversal stats, and submodule handling.

### Changed
- Traverse progress bar uses spinner mode instead of showing misleading pre-filter totals (e.g. "2132/83601").
- Traverse phase label changed from "Traversing files..." to "Scanning & filtering files...".

### Fixed
- Server tests now use real temp directories with `.git` folders for path validation (#69 compatibility).

### Docs
- Updated README CLI reference with `--index-only`, `-x`, and `--include-submodules` examples.
- Updated website docs (`cli-reference.md`, `configuration.md`, `getting-started.md`) with submodule handling, `.gitignore` documentation, and new output examples.
- Reorganized `docs/` directory: architecture docs into `docs/architecture/`, internals into `docs/internals/`.
- Removed stale one-time documents (PHASE_5_5_IMPLEMENTATION, GIT_INTELLIGENCE_AUDIT, MCP_AND_STATE_REVIEW, MCP_TOOLS_TEST_REPORT).

---

## [0.2.1] — 2026-04-10

### Added
- **`get_answer` MCP tool** (`tool_answer.py`) — single-call RAG over the wiki layer. Runs retrieval, gates synthesis on top-hit dominance ratio, and returns a 2–5 sentence answer with concrete file/symbol citations plus a `confidence` label. High-confidence responses can be cited directly without verification reads. Backed by an `AnswerCache` table so repeated questions on the same repository cost nothing on the second call.
- **`get_symbol` MCP tool** (`tool_symbol.py`) — resolves a fully-qualified symbol id (`path::Class::method`, also accepts `Class.method`) to its source body, signature, file location, line range, and docstring. Returns the rich source-line signature (with base classes, decorators, and full type annotations preserved) instead of the stripped DB form.
- **`Page.summary` column** — short LLM-extracted summary (1–3 sentences) attached to every wiki page during generation. Used by `get_context` to keep context payloads bounded on dense files. Added by alembic migration `0012_page_summary`.
- **`AnswerCache` table** — memoised `get_answer` responses keyed by `(repository_id, question_hash)` plus the provider/model used. Added by alembic migration `0013_answer_cache`. Cache entries are repository-scoped and invalidated by re-indexing.
- **Test files in the wiki** — `page_generator._is_significant_file()` now treats any file tagged `is_test=True` (with at least one extracted symbol) as significant, regardless of PageRank. Test files have near-zero centrality because nothing imports them back, but they answer "what test exercises X" / "where is Y verified" questions; the doc layer is the right place to surface those. Filtering remains available via `--skip-tests`.
- **Overview dashboard** (`/repos/[id]/overview`) — new landing page for each repository with:
  - Health score ring (composite of doc coverage, freshness, dead code, hotspot density, silo risk)
  - Attention panel highlighting items needing action (stale docs, high-risk hotspots, dead code)
  - Language donut chart, ownership treemap, hotspots mini-list
  - Decisions timeline, module minimap (interactive graph summary)
  - Quick actions panel (sync, full re-index, generate CLAUDE.md, export)
  - Active job banner with live progress polling
- **Background pipeline execution** — `POST /api/repos/{id}/sync` and `POST /api/repos/{id}/full-resync` now launch the full pipeline in the background instead of only creating a pending job. Concurrent runs on the same repo return HTTP 409.
- **Shared persistence layer** (`core/pipeline/persist.py`) — `persist_pipeline_result()` extracted from CLI, reused by both CLI and server job executor
- **Job executor** (`server/job_executor.py`) — background task that runs `run_pipeline()`, writes progress to the `GenerationJob` table, and persists all results
- **Server crash recovery** — stale `running` jobs are reset to `failed` on server startup
- **Async pipeline improvements** — `asyncio.wrap_future` for file I/O, `asyncio.to_thread` for graph building and thread pool shutdown, periodic `asyncio.sleep(0)` yields during parsing
- **Health score utility** (`web/src/lib/utils/health-score.ts`) — composite health score computation, attention item builder, and language aggregation for the overview dashboard

### Changed
- **`get_context` default is now `compact=True`** — drops the `structure` block, the `imported_by` list, and per-symbol docstring/end-line fields to keep the response under ~10K characters. Pass `compact=False` for the full payload (e.g. when you specifically need import-graph dependents on a large file).
- `init_cmd.py` refactored to use shared `persist_pipeline_result()` instead of inline persistence logic
- Pipeline orchestrator uses async-friendly patterns to keep the event loop responsive during ingestion
- Sidebar and mobile nav updated to include "Overview" link

- Monorepo scaffold: uv workspace with `packages/core`, `packages/cli`, `packages/server`, `packages/web`
- Provider abstraction layer: `BaseProvider`, `GeneratedResponse`, `ProviderError`, `RateLimitError`
- `AnthropicProvider` with prompt caching support
- `OpenAIProvider` with OpenAI Chat Completions API
- `OllamaProvider` for local offline inference (OpenAI-compatible endpoint)
- `LiteLLMProvider` for 100+ models via LiteLLM proxy
- `MockProvider` for testing without API keys
- `RateLimiter`: async sliding-window RPM + TPM limits with exponential backoff
- `ProviderRegistry`: dynamic provider loading with custom provider registration
- CI pipeline: GitHub Actions matrix on Python 3.11, 3.12, 3.13
- Pre-commit hooks: ruff lint + format, mypy, standard file checks
- **Folder exclusion** — three-layer system for skipping paths during ingestion:
  - `FileTraverser(extra_exclude_patterns=[...])` — pass gitignore-style patterns at construction time; applied to both directory pruning and file-level filtering
  - Per-directory `.repowiseIgnore` — traverser loads one from each visited directory (like git's per-directory `.gitignore`); patterns are relative to that directory and cached for efficiency
  - `repowise init --exclude/-x PATTERN` — repeatable CLI flag; patterns are merged with `exclude_patterns` from `config.yaml` and persisted back to `.repowise/config.yaml`
  - `repowise update` reads `exclude_patterns` from `config.yaml` automatically
  - Web UI **Excluded Paths** section on `/repos/[id]/settings`: chip editor, Enter-to-add input, six quick-add suggestions (`vendor/`, `dist/`, `build/`, `node_modules/`, `*.generated.*`, `**/fixtures/**`), empty-state message, gitignore-syntax tooltip; saved via `PATCH /api/repos/{id}` as `settings.exclude_patterns`
  - `helpers.save_config()` now round-trips `config.yaml` to preserve all existing keys when updating provider/model/embedder; accepts optional `exclude_patterns` keyword argument
  - `scheduler.py` logs `repo.settings.exclude_patterns` in polling fallback as preparation for future full-sync wiring
- 13 new unit tests in `tests/unit/ingestion/test_traverser.py` covering `extra_exclude_patterns` and per-directory `.repowiseIgnore` behaviour

---

## [0.2.0] — 2026-04-07

A large overhaul: faster indexing, smarter doc generation, transactional storage,
new analysis capabilities, and a completely revamped web UI that surfaces every
new signal — all without changing the eight MCP tool surface.

### Added

#### Pipeline & ingestion
- **Parallel indexing.** AST parsing now runs across all CPU cores via
  `ProcessPoolExecutor`. Graph construction and git history indexing run
  concurrently with `asyncio.gather`. Per-file git history fetched through a
  thread executor with a semaphore.
- **RAG-aware doc generation.** Pages are generated in topological order; each
  generation prompt now includes summaries of the file's direct dependencies,
  pulled from the vector store of already-generated pages.
- **Atomic three-store coordinator.** New `AtomicStorageCoordinator` buffers
  writes across SQL, the in-memory dependency graph, and the vector store, then
  flushes them as a single transaction. Failure in any store rolls back all three.
- **Dynamic import hint extractors.** The dependency graph now captures edges
  that pure AST parsing misses: Django `INSTALLED_APPS` / `ROOT_URLCONF` /
  `MIDDLEWARE`, pytest `conftest.py` fixture wiring, and Node/TS path aliases
  from `tsconfig.json` and `package.json` `exports`.

#### Analysis
- **Temporal hotspot decay.** New `temporal_hotspot_score` column on
  `git_metadata`, computed as `Σ exp(-ln2 · age_days / 180) · min(lines/100, 3)`
  per commit. Hotspot ranking now uses this score; commits from a year ago
  contribute ~25% as much as commits from today.
- **Percentile ranks via SQL window function.** `recompute_git_percentiles()`
  is now a single `PERCENT_RANK() OVER (PARTITION BY repo ORDER BY ...)` UPDATE
  instead of an in-Python sort. Faster and correct on large repos.
- **PR blast radius analyzer.** New `PRBlastRadiusAnalyzer` returns direct
  risks, transitive affected files, co-change warnings, recommended reviewers,
  test gaps, and an overall 0-10 risk score. Surfaced via `get_risk(changed_files=...)`
  and a new web page.
- **Security pattern scanner.** Indexing now runs `SecurityScanner` over each
  file. Findings (eval/exec, weak crypto, raw SQL string construction,
  hardcoded secrets, `pickle.loads`, etc.) are stored in a new
  `security_findings` table.
- **Knowledge map.** Top owners, "bus factor 1" knowledge silos (>80% single
  owner), and high-centrality "onboarding targets" with thin documentation --
  surfaced in `get_overview` and the web overview page.

#### LLM cost tracking
- New `llm_costs` table records every LLM call (model, tokens, USD cost).
- `CostTracker` aggregates session totals; pricing covers Claude 4.6 family,
  GPT-4.1 family, and Gemini.
- New `repowise costs` CLI: `--since`, `--by operation|model|day`.
- Indexing progress bar shows a live `Cost: $X.XXXX` counter.

#### MCP tool enhancements (still 8 tools -- strictly more capable)
- `get_risk(targets, changed_files=None)` -- when `changed_files` is provided,
  returns the full PR blast-radius report (transitive affected, co-change
  warnings, recommended reviewers, test gaps, overall 0-10 score). Per-file
  responses now include `test_gap: bool` and `security_signals: list`.
- `get_overview()` -- now includes a `knowledge_map` block (top owners, silos,
  onboarding targets).
- `get_dead_code(min_confidence?, include_internals?, include_zombie_packages?)` --
  sensitivity controls for false positives in framework-heavy code.

#### REST endpoints (new)
- `GET /api/repos/{id}/costs` and `/costs/summary` -- grouped LLM spend.
- `GET /api/repos/{id}/security` -- security findings, filterable by file/severity.
- `POST /api/repos/{id}/blast-radius` -- PR impact analysis.
- `GET /api/repos/{id}/knowledge-map` -- owners / silos / onboarding targets.
- `GET /api/repos/{id}/health/coordinator` -- three-store drift status.
- `GET /api/repos/{id}/hotspots` now returns `temporal_hotspot_score` and is
  ordered by it.
- `GET /api/repos/{id}/git-metadata` now returns `test_gap`.
- Job SSE stream now emits `actual_cost_usd` (running cost since job start).

#### Web UI (new pages and components)
- **Costs page** -- daily bar chart, grouped tables by operation/model/day.
- **Blast Radius page** -- paste files (or click hotspot suggestion chips) to
  see risk gauge, transitive impact, co-change warnings, reviewers, test gaps.
- **Knowledge Map card** on the overview dashboard.
- **Trend column** on the hotspots table with flame indicator (default sort).
- **Security Panel** in the wiki page right sidebar.
- **"No tests" badge** on wiki pages with no detected test file.
- **System Health card** on the settings page (SQL / Vector / Graph counts +
  drift % + status).
- **Live cost indicator** on the generation progress bar.

#### CLI
- `repowise costs [--since DATE] [--by operation|model|day]` -- new command.
- `repowise dead-code` -- new flags `--min-confidence`, `--include-internals`,
  `--include-zombie-packages`, `--no-unreachable`, `--no-unused-exports`.
- `repowise doctor` -- new Check #10 reports coordinator drift across all
  three stores. `--repair` deletes orphaned vectors and rebuilds missing graph
  nodes from SQL.

### Fixed
- C++ dependency resolution edge cases.
- Decision extraction timeout on very large histories.
- Resume / progress bar visibility for oversized files.
- Coordinator `health_check` falsely reporting 100% drift on LanceDB / Pg
  vector stores (was returning -1 for the count). Now uses `list_page_ids()`.
- Coordinator `health_check` returning `null` graph node count when no
  in-memory `GraphBuilder` is supplied. Now falls back to SQL `COUNT(*)`.

### Internal
- Three new Alembic migrations: `0009_llm_costs`, `0010_temporal_hotspot_score`,
  `0011_security_findings`.

### Compatibility
- Existing repositories must run migrations: `repowise doctor` will detect
  the missing tables and prompt; alternatively re-run `repowise init` to
  rebuild from scratch.
- The eight MCP tool names and signatures are backwards compatible -- new
  parameters are all optional.

---

## [0.1.31] — earlier

See git history for releases prior to 0.2.0.

---

[0.3.1]: https://github.com/repowise-dev/repowise/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/repowise-dev/repowise/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/repowise-dev/repowise/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/repowise-dev/repowise/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/repowise-dev/repowise/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/repowise-dev/repowise/compare/v0.1.31...v0.2.0
