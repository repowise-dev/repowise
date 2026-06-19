# Language Support

repowise analyses codebases written in many languages. Each language goes
through a multi-stage pipeline: file traversal, AST parsing, import
resolution, call graph construction, and heritage (inheritance) extraction.
Not every language has reached full coverage yet --- this page documents
exactly what works today, what is coming next, and how to add a new language.

---

## Support Tiers

Every language falls into one of five tiers. The tier determines which
pipeline stages produce meaningful output.

| Stage | Full | Partial | Scaffolded | Traversal | Config / Data |
|-------|:----:|:-------:|:----------:|:---------:|:-------------:|
| File discovery & git history | Y | Y | Y | Y | Y |
| AST symbol extraction | Y | Y | -- | -- | -- |
| Import resolution | Y | Y | -- | -- | -- |
| Call graph edges | Y | partial | -- | -- | -- |
| Heritage (extends/implements) | Y | partial | -- | -- | -- |
| Named bindings | Y | -- | -- | -- | -- |
| Code-health biomarkers | Y¹ | -- | -- | -- | -- |
| Dead code detection | Y | Y | Y | Y | -- |
| Semantic search & wiki pages | Y | Y | Y | Y | Y |

¹ **Code-health biomarkers** require a per-language complexity-walker map
(`analysis/health/complexity/languages.py`), independent of `.scm` parsing.
A language is only listed **Full** once it clears the
[code-health checklist](#code-health-biomarker-coverage) below — except
**Go**, whose class-level metrics (LCOM4 / god-class) are not computable
because Go methods attach to a type via an external receiver rather than
nesting in a class body; Go gets the function- and assertion-level
biomarkers but is footnoted on the class-level ones.

---

## Language Reference

### Full

Languages with complete pipeline coverage: AST parsing, import resolution,
call resolution, named bindings, heritage extraction, and docstrings.

| Language | Extensions | Entry Points | Import Style |
|----------|-----------|-------------|-------------|
| **Python** | `.py` `.pyi` | `main.py` `app.py` `__main__.py` `manage.py` `wsgi.py` `asgi.py` | `import x` / `from x import y` |
| **TypeScript** | `.ts` `.tsx` | `index.ts` `main.ts` `app.ts` `server.ts` | `import { x } from 'y'` / `export { x } from 'y'` & `export * from 'y'` re-export barrels / `require()` with tsconfig path aliases, npm/yarn/pnpm `workspaces`, and optional `.vue`/`.svelte`/`.astro` SFC probing |
| **JavaScript** | `.js` `.jsx` `.mjs` `.cjs` | `index.js` `main.js` `app.js` `server.js` | `import` / `require()` incl. CommonJS re-export shapes (`module.exports = require('./x')`, `exports.foo = require('./y')`, `Object.assign(module.exports, require('./z'), …)` — flagged as re-export hubs like ESM barrels) and member picks (`var x = require('./m').member`) |
| **Java** | `.java` | `Main.java` `Application.java` | `import pkg.Class` / `import pkg.*` / `import static pkg.Class.*` with Maven `pom.xml` reactor + Gradle `settings.gradle(.kts)` source-sets + `gradle/libs.versions.toml` discovery, JPMS `module-info.java` / `package-info.java` recognition, fan-out to every file in the imported package (siblings share importers), same-package implicit identifier resolution; JDK namespaces (`java.` / `javax.` / `jdk.`) are filtered with no node, `jakarta.` classifies as an external dependency |
| **Kotlin** | `.kt` `.kts` | `Main.kt` `Application.kt` | `import com.example.Foo` / `import com.example.*` sharing the JVM workspace index with Java (cross-language resolution, `.kt` under `src/main/java` recognised); `kotlin.` stdlib filtered with no node, `kotlinx.` classifies as an external dependency; same-package implicit identifier resolution |
| **Go** | `.go` | `main.go` `cmd/main.go` | `import "path"` with multi-module `go.mod` discovery (longest-prefix match); a package import fans out to **all** `.go` files in the package directory |
| **Rust** | `.rs` | `main.rs` `lib.rs` | `use crate::` / `use super::` / `use self::` with `Cargo.toml` |
| **C++** | `.cpp` `.cc` `.cxx` `.h` `.hpp` `.hxx` | `main.cpp` `main.cc` `WinMain` `wWinMain` `LLVMFuzzerTestOneInput` `DllMain` | `#include` with `compile_commands.json` resolution + CMake / Bazel workspace public-header maps + per-target include-dir search + sibling-TU fan-out (header-only targets fan out across their headers) + same-stem same-dir header ↔ implementation pairing edges |
| **C#** | `.cs` | `Program.cs` `Startup.cs` | `using Acme.Domain` / `global using` / `using static` / `using Alias = X.Y.Z` with `.csproj` / `.sln` / `Directory.Build.props` resolution; `partial` class co-fragments linked bidirectionally; nested types resolve one level (`Outer.Inner`) |

All nine languages support:
- Tree-sitter AST parsing with dedicated `.scm` query files
- Three-tier call resolution (same-file, cross-file, global stem match)
- Named binding extraction (mapping imported names to source symbols)
- Heritage extraction (class/interface/trait/record inheritance chains)
- Docstring extraction (Python, JSDoc, GoDoc, Rustdoc, Javadoc, Doxygen, XML doc)
- Framework-aware edges (Django, FastAPI, Flask for Python; tsconfig path aliases for TS/JS; pytest fixture detection; ASP.NET controllers / minimal API / EF Core DbContext for C#; Spring Boot DI + `@Bean` factories for Java/Kotlin; Rails routes + ActiveRecord relationships; Laravel routes + service providers + Eloquent; TYPO3 convention files (`ext_localconf.php`, `Configuration/TCA/*`, `JavaScriptModules.php` registrations) for PHP; Express `app.use(router)` + NestJS `@Module` arrays; Gin/Echo/Chi router + stdlib `net/http` (`http.HandleFunc` / `mux.Handle`) + gRPC `RegisterXxxServer` → handler/impl files for Go; Axum/Actix `.route` → handler files for Rust)
- Per-language dynamic-hint extractors (Django/Pytest/Node/`importlib` string-import registries for Python+JS/TS; .NET DI/Activator/InternalsVisibleTo for C#; Spring `getBean`/`@Bean` factories for Java/Kotlin; Ruby `send`/`const_get`/`define_method`/`delegate`; PHP `call_user_func`/`ReflectionClass`/container `get`; Scala `Class.forName`/`given`/`implicit val`; Swift `NSClassFromString`/`Selector`/`#selector`/KVC; C function-pointer assignment + `dlopen`/`dlsym`; Luau `game:GetService`/`setmetatable __index`; Go `reflect.TypeOf`/`reflect.New`/`reflect.ValueOf`/`plugin.Open`/`plugin.Lookup`; Alpine.js `Alpine.data`/`store`/`magic`/`directive` registrations → handler files for JS)
- For C# only: MSBuild project graph (`<ProjectReference>` / `<PackageReference>`), namespace → file mapping across projects, `global using` / `using static` / `using alias` propagation, ASP.NET HTTP and gRPC-dotnet contract extraction in workspace mode, cross-repo `<ProjectReference>` and internal-NuGet detection, host-builder extension method resolution (`app.MapCatalogApi()` / `services.AddCatalogServices()` on any C# repo, not just ASP.NET), `nameof(Type)` references resolved as dynamic uses, local `var x = new T()` property reads bound to the defining file, and CommunityToolkit MVVM source-generator synthesis (`[ObservableProperty]` fields → PascalCase property, `[RelayCommand]` methods → `<Name>Command`)
- For Go only: a package-granular workspace index (`GoPackageIndex`) built as a warmup phase — a package import resolves to every file in the package directory, and package-qualified calls (`pkg.Func`) / same-package bare calls resolve across all of a package's files; type-reference resolution emits `type_use` edges for field / parameter / return / composite-literal types (unwrapping `*T` / `[]T` / `map[K]V` / `pkg.T` / generics); package-aware dead-code reachability (a file is reachable when its package is imported or is a `package main` entry); structural interface satisfaction — concrete types are matched to the interfaces their method set satisfies (embedded interfaces expanded) and emit `method_implements` edges so interfaces reached only through implementors are not flagged dead; and Go never-flag conventions (`*_test.go`, `cmd/**/main.go`, `doc.go`, `magefile.go`, generated `*.pb.go` / `*_string.go` / `zz_generated*` / `bindata.go`) plus `init` / `TestMain` / `Test*` / `Benchmark*` / `Example*` / `Fuzz*` entry handling
- For C/C++ only: a unified `CppWorkspaceIndex` warmup parses CMake reactors (`add_subdirectory`, `add_executable` / `add_library`, `target_sources`, `target_include_directories`, `target_compile_definitions`, `target_link_libraries`, `option`/`if(...)` conditional sources, CMake File API `build/.cmake/api/v1/reply/` JSON), Bazel `BUILD` files (`cc_binary` / `cc_library` / `cc_test` / `cc_proto_library` / `cc_grpc_library` / `cc_fuzz_test`), and `compile_commands.json`; the index drives a six-step include resolver (compile_commands → workspace public-header map → per-target include-dirs → importer-relative → stdlib filter → stem fallback) and fans `#include` edges out to every sibling TU in the same target so internal headers are reachable without a direct importer; project-specific export macros (`LEVELDB_EXPORT` / `SEASTAR_API` / `*_EXPORT` / `*_API` / `*_PUBLIC`) are discovered from `target_compile_definitions` and `#define X __declspec(dllexport)` patterns and re-applied as visibility markers during the warmup, alongside the literal `__declspec(dllexport)` / `__attribute__((visibility("default")))` / `EMSCRIPTEN_KEEPALIVE` / `WASM_EXPORT` / `export_name` / `((used))` markers (so JS↔WASM exports and project-macro-exported public APIs are not flagged dead); type-reference resolution emits `type_use` edges for parameter / field / return / template-argument types (unwrapping `std::vector` / `std::optional` / `std::shared_ptr` / friends and stripping pointer / reference / array tails) and consults the workspace's same-target sibling set for unqualified resolution; static-initialiser registration macros (`PYBIND11_MODULE`, `BOOST_PYTHON_MODULE`, `REGISTER_OP`, `REGISTER_KERNEL_BUILDER`, `BOOST_CLASS_EXPORT`, `PLUGINLIB_EXPORT_CLASS`, `RCLCPP_COMPONENTS_REGISTER_NODE`, `Q_OBJECT` / `Q_GADGET` / `QML_ELEMENT`, gflags `DEFINE_*`, `ABSL_FLAG`, `__attribute__((constructor/used))`, `[[gnu::retain/used]]`, `JNI_OnLoad`, `NAPI_MODULE`) synthesize the symbols they emit at compile time and stamp their TU as an entry point so the file is not flagged unreachable; framework edges cover the C++ test, benchmark, and fuzzing ecosystem — **GoogleTest** (`TEST` / `TEST_F` / `TEST_P` / `TYPED_TEST` with `TEST_F(FixtureClass, …)` emitting a `type_use` edge to the fixture's defining header), **Catch2** (`TEST_CASE` / `TEST_CASE_METHOD` / `SCENARIO`), **Boost.Test** (`BOOST_AUTO_TEST_CASE` / `BOOST_FIXTURE_TEST_CASE(case, Fixture)` with fixture rescue), **doctest** (`DOCTEST_TEST_CASE`), **Google Benchmark** (`BENCHMARK` / `BENCHMARK_F` / `BENCHMARK_MAIN`), **libFuzzer** (`LLVMFuzzerTestOneInput` / `LLVMFuzzerInitialize`); C++ dynamic-hint extractor (function-pointer assignments, designated-initializer field tables, `dlopen` / `dlsym` / `LoadLibrary` / `GetProcAddress`, Qt old-style `connect(SIGNAL(sig()), SLOT(slot()))` and new-style `connect(&Sender::sig, &Recv::slot)`); C++ contract methods (constructor / destructor / operator overloads, conversion operators (`operator bool`, `operator T`), STL customisation points (`begin` / `end` / `cbegin` / `cend` / `rbegin` / `rend` / `size` / `empty` / `data` / `swap` / `hash_value` / `to_string`), coroutine machinery (`await_ready` / `await_suspend` / `await_resume` / `promise_type` / `initial_suspend` / `final_suspend` / `return_void` / `return_value` / `yield_value` / `unhandled_exception`), `std::format` / `std::error_code` customisation); directory-granular reachability with **header-reached-by-symbol-reference rescue**, `int main` / `WinMain` / `wWinMain` / `wmain` / `LLVMFuzzerTestOneInput` / `LLVMFuzzerInitialize` / `DllMain` binary-entry recognition, sibling-rescue (internal header reached via its `.cc` neighbour), same-directory main-carrier rescue (helper files alongside `main.cc` are not dead), and conditional-compile alternative pairing (`env_posix.cc` ↔ `env_windows.cc`, `crypto_openssl.cc` ↔ `crypto_gnutls.cc`); never-flag conventions for `apps/**`, `demos/**`, `examples/**`, `samples/**`, `benchmarks/**`, `bench/**`, `tools/**`, `tests/{perf,fuzz,unit,manual,integration}/**`, `*_test.{cc,cpp}` / `*_unittest.*` / `*_perf.*` / `*_benchmark.*` / `*_fuzz.*` suffix globs, skeleton convention headers (`port_example.h`, `*_example.{h,hpp,cc}`), build directory roots (`build/**`, `cmake-build-*/**`, `_deps/**`, `out/{build,Debug,Release}/**`), generated source patterns (`moc_*.{cpp,cc}`, `ui_*.h`, `qrc_*.{cpp,cc}`, `*.moc`, `*.pb.{cc,h}`, `*.pb-c.{c,h}`, `*.grpc.pb.{cc,h}`, `*.capnp.{c++,h}`, `*.flatbuffers.h`, `*_generated.h`, `*.tab.{c,h}`, `*.yy.c`, `*_lex.cc`, `*_wrap.{cxx,cpp}`, `*.cython.cpp`), vendor roots (`external/**`, `extern/**`, `contrib/**`, `submodules/**`, `vendor/**`, `third_party/**`, `deps/**`), precompiled-header conventions (`pch.{h,cc,cpp}`, `stdafx.*`, `PrecompiledHeader.{cc,cpp}`), and Windows COM / ATL DLL entry points (`DllMain`, `DllGetClassObject`, `DllCanUnloadNow`, `DllRegisterServer`, `DllUnregisterServer`, `DllGetActivationFactory`); a per-symbol-use dead-code check rescues headers whose declared types are referenced as parameter / field / return / template-argument / `extends` / `implements` even with no `#include` chain
- For JavaScript only: esbuild/rollup bundle outputs (`*.bundle.js`), minified artifacts (`*.min.js`), LiveReload generated browser-global scripts (`livereload/gen/*`, `livereload.js`) and WASM JS glue (`*wasm_exec.js`) are never flagged as dead (build products / browser-loaded, never module-imported)
- For TypeScript / JavaScript only: a workspace-granular index (`TsWorkspaceIndex`) built as a warmup phase that surfaces every file reachable through a `package.json` `exports` map (including `"./locales/*"`-style wildcards) as an entry point so downstream npm consumers don't read as unreachable; type-reference resolution emits `type_use` edges for parameter / field / return / generic-constraint / type-alias-RHS / heritage clauses (unwrapping `Promise<T>`, `T[]`, `Pick<T,K>`, union/intersection, conditional/mapped types) so interfaces consumed only as types are not flagged dead; same-file rescue exempts symbols whose only consumer is another declaration in the same module; convention never-flag globs for `*.test.*` / `*.spec.*` / `*.stories.*` / `*.bench.*` / `__tests__/**` / `__mocks__/**` / Next.js app-router files (`page.tsx`, `layout.tsx`, `route.ts`, `middleware.ts`, `instrumentation.ts`, route segment configs) / SvelteKit `+page` / `+layout` / `+server` / Remix `entry.client` / `entry.server` / `next-env.d.ts` / `*.gen.ts` / `generated/**`; experimental sub-package rescue marks every source file in directories named `bench` / `benchmarks` / `treeshake` / `examples` / `demos` / `samples` / `playground` / `scratch` / `scripts` as a live entry (these dirs invoke their files via runtime CLI arguments no static scan can follow); npm-script entry detection — parses every `package.json` `scripts.*` command for paths (`tsx X.ts` / `bun run X.mts` / `rollup -c X.js`), quoted glob arguments (prettier / eslint scopes), and bare directory tokens; MDX `import` scan + `vitest.config` `include` glob scan as belt-and-suspenders for entry surfaces the static parser can't see; framework edges for Next.js App Router (`app/**/{page,layout,route,middleware,…}`), Hono / Fastify / Koa / Elysia router DSLs (`.get('/x', handler)`), Remix / SvelteKit / Astro filesystem-convention routes, and tRPC procedure registries (`.query(handler)` / `.mutation(handler)`); JSX-namespace types declared inside a `namespace JSX` file are exempt from `unused_export` (consumed by tsc via `jsxImportSource`, never imported as values)
- For XAML only: `<ResourceDictionary Source="..."/>` and `MergedDictionaries` entries resolve across `pack://application:,,,/`, `ms-appx:///`, repo-rooted and relative URIs, emitting xaml→xaml `dynamic_uses` edges
- For JVM (Java + Kotlin together) only: a unified `JvmWorkspaceIndex` warmup parsing Maven `pom.xml` reactors and Gradle `settings.gradle(.kts)` + source-sets + `gradle/libs.versions.toml` version catalogs; one walk groups every `.java` and `.kt` file by package directory so `import com.foo.Bar` fans out to every defining file in the package (siblings share importers), `import com.foo.*` / `import static com.foo.Bar.*` expand to all matching files, same-package implicit identifier access is honoured, and Kotlin↔Java cross-language resolution links callers across both source-roots (including `.kt` files hosted under `src/main/java`); type-reference resolution emits `type_use` edges for parameter / field / return / generic / `new T()` types (unwrapping arrays, generics, nullables), plus Kotlin `companion object` / `object` singleton / method-reference (`Foo::bar`) / `sealed … permits` resolution; source-generator-equivalent synthesis emits the symbols Lombok (`@Data` / `@Value` / `@Builder` / `@RequiredArgsConstructor` / `@Slf4j` / `@UtilityClass` / friends), Java `record`, Kotlin `data class` / `enum class` / `object`, and MapStruct / AutoValue / Immutables would produce at compile time — so a Lombok-only Spring service no longer reads its injected fields as unused; framework edges cover Spring (stereotypes with meta-annotation resolution; `@RequestMapping` family routing; Spring Data `JpaRepository` / `Crud` / `Mongo` / `R2dbc` / `Elasticsearch` / `Neo4j` / `Couchbase` / `Cassandra` interfaces and their derived-query methods as entry points; `@Bean` factories; single-constructor + `@Autowired` / `@Inject` / `@Resource` DI; Lombok-RAC/AAC constructor inference), Jakarta (JAX-RS `@Path`, CDI scopes, Servlet 3+ `@WebServlet`/`@WebFilter`/`@WebListener`, JPA `@Entity` + `@OneToMany`/`@ManyToOne`/`@ManyToMany`/`@OneToOne` association edges; both `javax.*` and `jakarta.*`), Quarkus (entry stereotypes + SmallRye `@Incoming("topic")` ↔ `@Outgoing("topic")` cross-linking), Micronaut (DI / routing, gated on Micronaut imports to disambiguate Spring's collisions), and Android (`AndroidManifest.xml` `android:name` references emit edges to the named class); JVM dynamic hints capture `Class.forName`, Mockito / mockk, MapStruct `Mappers.getMapper`, `SpringApplication.run` and Kotlin `runApplication<>`, Jackson / Gson `readValue` / `fromJson` / `treeToValue`; package-aware dead-code reachability rescues sibling-rescued packages, stereotype-annotated classes, `main(String[])` carriers, and FQNs listed in `META-INF/services/*` / `META-INF/spring.factories` (Boot 2) / `META-INF/spring/...AutoConfiguration.imports` (Boot 3) / JPMS `provides … with …` — all resolved during the workspace warmup; JVM never-flag patterns cover `module-info.java`, `package-info.java`, every conventional test source-set (`src/test/**`, `src/integrationTest/**`, `src/intTest/**`, `src/it/**`, `src/jmh/**`, generic `src/*Test/**`), generated source roots (`build/generated/**`, KAPT/KSP, `target/generated-sources/**`, GraalVM `aotSources`), generated suffix conventions (`Dagger*`, `AutoValue_*`, JPA `*_.java` metamodel, `*Grpc.java`, protoc `*OuterClass.java`), JUnit / TestNG / Spock / ArchUnit test-naming + annotation-marked entry points (`@Test` / `@PostConstruct` / `@EventListener` / `@Scheduled` / `@JmsListener` / `@KafkaListener` / `@RabbitListener` / `@SqsListener` / `@Incoming` / `@Outgoing` / `@Mojo` / `@Bean` / …), and JVM contract methods (`equals` / `hashCode` / `toString` / `compareTo` / `clone` / serialization `readObject` / `writeObject` / `serialVersionUID` / Lombok `canEqual` / Kotlin `componentN` / `copy` / enum `values` / `valueOf`)

#### Code-health biomarker coverage

The code-health layer scores every source file from deterministic
biomarkers (complexity, cohesion, test-quality, …). These run off a
per-language complexity-walker map
(`analysis/health/complexity/languages.py`) that is **independent** of the
`.scm` ingestion queries — a language can parse perfectly for the graph yet
still need this map before health biomarkers fire. To keep "Full" meaning
"health works", a language must clear this checklist (each item has a green
fixture under `tests/fixtures/lang_samples/<lang>/` + a walker test):

1. **Control flow** — `if` / loop / `switch`/`match`/`when` / `try`-`catch`
   nodes mapped → McCabe CCN, nesting depth, cognitive complexity.
2. **Boolean operators** — `&&` / `||` (or their text form) counted toward
   CCN and `complex_conditional`.
3. **Class metrics** — `class_kinds` set so `method_count` / `total_nloc` /
   `max_method_ccn` aggregate (feeds `god_class`). *(N/A for Go — no
   class-grouping node.)*
4. **LCOM4 cohesion** — `self_identifiers` + `member_access_kinds` set so
   explicit-receiver member access is detected (feeds `low_cohesion`).
   Receiver-less idioms degrade to "no signal", never a false positive.
   *(N/A for Go.)*
5. **Assertion blocks** — `assert_kinds` / `assert_call_kinds` set so runs
   of consecutive assertions are detected on test files (feeds
   `large_assertion_block` / `duplicated_assertion_block`).

| Language | Control flow | Class metrics | LCOM4 | Assertions |
|----------|:---:|:---:|:---:|:---:|
| Python | Y | Y | Y | Y |
| TypeScript / JavaScript | Y | Y | Y | Y |
| Java | Y | Y | Y | Y |
| Kotlin | Y | Y | Y | Y |
| Go | Y | — | — | Y |
| Rust | Y | Y | Y | Y |
| C++ | Y | Y | Y | Y |
| C# | Y | Y | Y | Y |

### Good

AST parsing, symbol extraction, import resolution, call resolution, named
bindings, heritage extraction (including Ruby mixins, Rust derive, Swift
extension conformance, PHP trait use), and docstrings. Dedicated import
resolvers for each language.

| Language | Extensions | Entry Points | Import Style |
|----------|-----------|-------------|-------------|
| **C** | `.c` | `main.c` | `#include` with `compile_commands.json` (shares C++ grammar) |
| **Ruby** | `.rb` | `main.rb` `app.rb` `config.ru` | `require 'mod'` / `require_relative './mod'` with `$LOAD_PATH` convention probing (`require 'sinatra/base'` → `lib/sinatra/base.rb`, including every sub-gem's `lib/` in monorepos), Ruby-stdlib requires filtered with no node, Gemfile/gemspec dependencies labelled as gem externals, rspec directory-mirror edges (`spec/lib/x_spec.rb` → `lib/x.rb`), plus Rails / Zeitwerk autoloading (gated on `config/application.rb`) |
| **Swift** | `.swift` | `main.swift` `App.swift` | `import Foundation` with SPM `Package.swift` `targets:` → directory mapping; intra-module type references link same-target files (Swift has no intra-module imports by design); `@main` / `@UIApplicationMain` / `@NSApplicationMain` flag entry points; `@_exported import` marks re-exports |
| **Scala** | `.scala` | `Main.scala` `App.scala` | `import pkg.Foo`, brace imports `{A, B => C}` (expanded per selected name, hidden imports skipped), wildcards (`pkg._` / Scala 3 `pkg.*`), and package imports — resolved through the shared JVM workspace index (chained package clauses, Scala ↔ Java cross-language, same-package implicit identifiers) with SBT `build.sbt` / Mill `build.sc` multi-project parsing as fallback; `scala.` and JDK namespaces filtered |
| **PHP** | `.php` | `index.php` `public/index.php` | `use Foo\Bar\Baz` with composer.json `autoload.psr-4` longest-prefix resolution |

### Config / Data

Non-code files included in the file tree and wiki. Special handlers extract
endpoints or targets where applicable.

| Language | Extensions / Filenames | Special Handler |
|----------|----------------------|----------------|
| **OpenAPI** | YAML/JSON with `openapi` or `swagger` key | Extracts API paths and schemas |
| **Dockerfile** | `Dockerfile` | Extracts stages and exposed ports |
| **Makefile** | `Makefile` `GNUmakefile` | Extracts targets |
| **Protobuf** | `.proto` | -- |
| **GraphQL** | `.graphql` `.gql` | -- |
| **Terraform** | `.tf` `.hcl` | -- |
| **YAML** | `.yaml` `.yml` | -- |
| **JSON** | `.json` | -- |
| **TOML** | `.toml` | -- |
| **Markdown** | `.md` `.mdx` `.markdown` `.mdown` | -- |
| **AsciiDoc** | `.adoc` `.asciidoc` | -- |
| **SQL** | `.sql` | -- |
| **Shell** | `.sh` `.bash` `.zsh` | -- |

### Partial (Luau — Roblox)

| Language | Extensions | Entry Points | Import Style |
|----------|-----------|-------------|-------------|
| **Luau** | `.luau` `.lua` | `init.luau` `init.lua` | `require(script.Parent.X)` / `require(script.X)` / `require(game.Service.Path)` / `require("rel/path")` |

AST parsing, symbol extraction (functions, Luau type aliases), and
`require(...)` call capture are wired. Import resolution handles string
literals, `script`/`script.Parent` relative instance paths (including the
`:WaitForChild("X")` / `:FindFirstChild("X")` idioms), absolute Roblox
instance paths (`game.<Service>...`, plus the `game:GetService("X")` idiom)
through Rojo's `default.project.json` tree mapping, and `@alias` requires
through `.luaurc` `aliases` maps (nearest declaration wins, child overrides
parent). Repos without a Rojo project file / `.luaurc` keep the conservative
external-node fallback (issue #52 is closed by both halves).

### Lightweight (regex-tier import graphs)

These languages have no AST parsing (no symbols, calls, or heritage) but
get a real file-level import graph from a regex tier: import statements
are extracted with per-language regexes and resolved against a declared
module-name index (declaration scan + path-convention inverse). The
knowledge graph runs in flow/sparse mode on the resulting density —
honest file-to-file dependencies, no symbol-level claims.

| Language | Extensions | Import Forms Resolved |
|----------|-----------|----------------------|
| **Elixir** | `.ex` `.exs` | `alias` / `import` / `use` / `require` (incl. `Foo.{Bar, Baz}` brace groups) → `defmodule` index + Mix `lib/` path convention (umbrella `apps/*/lib` included); Elixir/OTP stdlib filtered after local-miss |
| **Dart** | `.dart` | `import` / `export` (re-export) / `part` / `part of` URIs; `package:` URIs via every `pubspec.yaml` `name:` (monorepos included); `dart:` SDK URIs filtered; foreign packages → `external:pub:<name>` |
| **Clojure** | `.clj` `.cljc` `.cljs` | `(:require …)` / `(:use …)` vectors and bare specs (string/comment-safe) → `(ns …)` index + classpath convention (dashes ↔ underscores); `clojure.*`/`cljs.*` filtered |
| **Haskell** | `.hs` `.lhs` | `import [safe] [qualified] ["pkg"] Foo.Bar` → `module` declaration index + trailing-PascalCase path inverse (handles any hs-source-dirs without parsing `.cabal`); base-ish namespaces filtered after local-miss |
| **Erlang** | `.erl` `.hrl` | `-include` / `-include_lib` / `-behaviour` + module-qualified calls (`mod:fun(`) → `-module()` index; qualified calls are strict local-hit-or-drop (never mint externals) |
| **F#** | `.fs` `.fsi` `.fsx` | `open` → file-level `namespace`/`module` index (unambiguous single-file declarations only), plus the fsproj `<Compile Include>` compile-order dependency spine (a real F# constraint: files may only reference earlier files) |

### Structural (git + file tree only)

These languages are tracked in git history (blame, hotspot analysis,
co-change detection) but have no AST parsing or import resolution. Files
appear in the wiki as traversal-level entries, and the knowledge graph
runs in **structural mode** for repos dominated by them: the tour
orients by directory structure, naming conventions, and git evidence —
it never claims an execution flow it cannot see.

Objective-C, R, Zig, Julia, Elm, OCaml, Crystal, Nim, D

---

## How the Pipeline Processes a File

```
File discovered by FileTraverser
        |
        v
Extension/filename -> LanguageTag  (via LanguageRegistry)
        |
        +-- Config/data language?  -> empty ParsedFile (passthrough)
        +-- Special format?        -> special_handlers.py (OpenAPI/Dockerfile/Makefile)
        +-- Has grammar?           -> tree-sitter AST parsing
                |
                v
        .scm query extracts:
          @symbol.def / @symbol.name    -> Symbol nodes
          @import.statement / @import.module -> Import edges
          @call.target / @call.receiver     -> Call edges
                |
                v
        Per-language extractors:
          - Named bindings (import name -> source symbol)
          - Heritage (extends/implements/traits)
          - Docstrings (Python, JSDoc, GoDoc, Rustdoc, Javadoc)
          - Visibility (public/private/protected)
                |
                v
        GraphBuilder resolves imports:
          Python: dotted module paths via a source-root-aware module index
                  (src/ + monorepo packages/*/src + PEP 420 namespace
                  packages), __init__.py re-export barrels, stem fallback
          TS/JS:  relative paths, tsconfig aliases, workspace exports,
                  `export ... from` re-export barrels, node_modules
          Go:     go.mod module path stripping
          Rust:   crate::/self::/super::, mod.rs probing
          C/C++:  compile_commands.json include directories
          Lightweight tier (Elixir/Dart/Clojure/Haskell/Erlang/F#):
                  regex-extracted imports vs a declared-module-name index
          Other:  stem-map fallback (filename matching)
                |
                v
        Graph analysis:
          PageRank, community detection, dead code, execution flows
```

---

## Adding a New Language

The pipeline is fully modular. Language identity data lives in the
centralised `LanguageRegistry`, per-language extraction logic lives in
`extractors/`, and per-language import resolution lives in `resolvers/`.
Adding a new language touches these places:

### Step 1: Add a `LanguageSpec` module

Language identity data lives in the `languages/specs/` package — **one module
per language**. Create
`packages/core/src/repowise/core/ingestion/languages/specs/mylang.py` exporting
a single `SPEC`:

```python
"""LanguageSpec for mylang."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="mylang",
    display_name="MyLang",
    extensions=frozenset({".ml"}),
    grammar_package="tree_sitter_mylang",       # PyPI package name
    scm_file="mylang.scm",                      # query file name
    heritage_node_types=frozenset({"class_declaration"}),
    entry_point_patterns=("main.ml",),
    manifest_files=("mylang.toml",),
    shebang_tokens=("mylang",),
    builtin_calls=frozenset({"print", "len"}),  # filter from call graph
    builtin_parents=frozenset({"Object"}),       # filter from heritage
    color_hex="#AB47BC",
)
```

Then register it in `languages/specs/__init__.py` by importing the module and
slotting it into the `ALL_SPECS` tuple. **Order matters** — `LanguageRegistry`
builds its extension map first-spec-wins, so place more specific languages
ahead of ones that share an extension (e.g. TypeScript before JavaScript).
The `LanguageRegistry` in `registry.py` consumes `ALL_SPECS`; you never edit
the registry itself to add a language.

### Step 2: Add the `LanguageTag`

Add `"mylang"` to the `LanguageTag` Literal type in
`packages/core/src/repowise/core/ingestion/models.py`.

### Step 3: Write a tree-sitter query file

Create `packages/core/src/repowise/core/ingestion/queries/mylang.scm` using
tree-sitter S-expression syntax. Follow the capture-name conventions:

| Capture | Purpose | Required? |
|---------|---------|-----------|
| `@symbol.def` | Full definition node (line numbers, kind lookup) | Yes |
| `@symbol.name` | Name identifier | Yes |
| `@symbol.params` | Parameter list | No |
| `@symbol.modifiers` | Decorators / visibility modifiers | No |
| `@symbol.receiver` | Go-style method receiver | No |
| `@import.statement` | Full import node | Yes |
| `@import.module` | Module path being imported | Yes |
| `@call.target` | Function/method being called | No (enables call graph) |
| `@call.receiver` | Object the call is made on | No |
| `@call.arguments` | Call arguments | No |

Look at existing `.scm` files for examples --- `python.scm` and
`typescript.scm` are good starting points.

### Step 4: Add a `LanguageConfig` entry

Add a parser configuration to `LANGUAGE_CONFIGS` in
`packages/core/src/repowise/core/ingestion/language_configs.py` (re-exported
from `parser.py` for back-compat):

```python
"mylang": LanguageConfig(
    symbol_node_types={
        "function_definition": "function",
        "class_definition": "class",
    },
    import_node_types=["import_statement"],
    export_node_types=[],
    visibility_fn=public_by_default,  # from extractors.visibility
    parent_extraction="nesting",
    parent_class_types=frozenset({"class_definition"}),
    entry_point_patterns=["main.ml"],
),
```

### Step 5: Add the tree-sitter grammar dependency

Add the grammar package to `pyproject.toml`:

```toml
[project]
dependencies = [
    # ...
    "tree-sitter-mylang>=0.23,<1",
]
```

### Step 6 (optional): Binding extractor

For full-tier support, add a `extract_mylang_bindings()` function in
`packages/core/src/repowise/core/ingestion/extractors/bindings.py` and
register it in the `extract_import_bindings()` dispatcher. Without this,
imports are still resolved but named-binding-level call resolution won't
work.

### Step 7 (optional): Heritage extractor

Add a `_extract_mylang_heritage()` function in
`packages/core/src/repowise/core/ingestion/extractors/heritage.py` and
register it in the `HERITAGE_EXTRACTORS` dict. Without this, inheritance
chains won't appear in the graph.

### Step 8 (optional): Import resolver

If the language has a non-trivial import system, create a resolver in
`packages/core/src/repowise/core/ingestion/resolvers/mylang.py` and
register it in the `_RESOLVERS` dict in `resolvers/__init__.py`. For simple
languages, the generic stem-map fallback (matching by filename) works out
of the box.

### Verify

```bash
# Run the parser tests
pytest tests/ -k "mylang or sample_repo" -x

# Index a real project
repowise init /path/to/mylang-project
```

No changes are needed to `traverser.py`, `dead_code.py`,
`page_generator.py`, `cost_estimator.py`, or any other consumer file ---
they all derive their language sets from the registry automatically.

### Optional language-specific passes

Several pluggable hooks let a language opt into deeper resolution
without touching the shared pipeline files:

- `graph_warmups.py` --- register a one-time pre-import warmup (e.g.
  building a project index) so its cost shows up as its own phase
  instead of inflating `graph.imports`.
- `type_ref_resolution._STRATEGIES` --- register a strategy that
  resolves parameter-type captures (`@param.type` in the language's
  `.scm`) to file-level type-use edges. Drives DI-aware analysis.
- `languages/<lang>_member_reads.py` --- emit `reads` edges for
  property / member access. Used today for C# `var x = new T()`
  locals; the same shape applies to any statically-typed language.
- `extractors/synthetic_symbols.py` --- recognise source-generator
  attributes and emit the symbols the generator would produce
  at compile time. Used today for CommunityToolkit MVVM
  (`[ObservableProperty]`, `[RelayCommand]`); the same shape fits
  Java Lombok, Kotlin `@Parcelize`, etc.
- `extractors/visibility.py::refine_<lang>_visibility` --- node-aware
  visibility refinement for languages where access is dictated by AST
  context (C/C++ access specifiers, storage class, export attributes)
  rather than modifier text alone.
- `graph_warmups.py` `is_never_flag` stamping --- read the language's
  build manifest during warmup and set `is_never_flag=True` on file
  nodes the manifest declares as a non-primary / secondary target.
  The dead-code analyzer consults this attribute in `_should_never_flag`,
  so each repo's own build files teach the analyzer what to ignore
  without extending the hardcoded glob list. Used today by `_warmup_jvm`
  to exempt every file under a Gradle non-`main` source set
  (`testFixtures`, `integrationTest`, `javaPoet`, `jcstress`, `jmh`,
  custom names) — Caffeine's `javaPoet/` and `jcstress/` are picked up
  automatically without us knowing the names exist. The same shape fits
  Rust (`[[example]]` / `[[bench]]` / `[[bin]]` targets in `Cargo.toml`),
  C# (projects with `<Sdk>Microsoft.NET.Sdk.Test</Sdk>` or
  `Microsoft.NET.Test.Sdk` references), TS/JS (workspace `packages/*`
  declared `"private": true`), and Go (`//go:build integration`-gated
  files) — any language that already builds a workspace index can opt
  in by stamping the attribute during its warmup.
- `analysis/health/complexity/languages.py` --- the code-health
  complexity walker keeps its own per-language node-type map
  (`LanguageNodeMap`), independent of the ingestion `.scm` queries. Add a
  map for a language to get McCabe complexity, nesting, cognitive
  complexity, and the per-function biomarkers; optionally set
  `class_kinds` / `self_identifiers` / `member_access_kinds` to also get
  class-level metrics (LCOM4 cohesion, god-class), and
  `assert_kinds` / `assert_call_kinds` to get test-quality assertion-block
  smells. All tiers are purely additive and degrade safely — an unmapped
  language simply produces no health findings rather than wrong ones.
  Control-flow and assertion maps ship for all nine full-tier languages
  (Python, TypeScript, JavaScript, Go, Java, Kotlin, Rust, C++, C#);
  class-level maps for all of those except Go (no class-grouping node).
  See `complexity/README.md` for the extension recipe and the LCOM4
  heuristic's limits.

---

## Architecture

The language pipeline is fully modular. Per-language code lives in
dedicated subpackages — adding a new language means dropping a file
into each subpackage rather than editing monoliths.

```
ingestion/
  languages/           # LanguageRegistry + LanguageSpec (identity data)
    spec.py            #   LanguageSpec dataclass (the schema)
    registry.py        #   LanguageRegistry lookup interface + REGISTRY singleton
    specs/             #   one module per language, each exporting `SPEC`
      __init__.py      #     aggregates every SPEC into ordered `ALL_SPECS`
      python.py  typescript.py  go.py  rust.py  csharp.py  …  (44 languages)
    python_modules.py  #   dotted-module ↔ file index (src / monorepo / PEP 420)
  extractors/          # Per-language AST extraction
    visibility.py      #   symbol visibility (public/private/protected)
    signatures.py      #   human-readable signature building
    docstrings.py      #   module + symbol docstring extraction
    bindings/          #   import name + alias binding extraction (per-lang)
      __init__.py      #     extract_import_bindings dispatcher
      python.py  ts_js.py  go.py  rust.py  java.py  kotlin.py
      ruby.py    csharp.py swift.py scala.py php.py cpp.py
    heritage/          #   inheritance/interface/trait extraction (per-lang)
      __init__.py      #     extract_heritage + HERITAGE_EXTRACTORS dispatcher
      python.py  ts_js.py  java.py  go.py    rust.py  cpp.py
      kotlin.py  ruby.py   swift.py csharp.py scala.py php.py
  resolvers/           # Per-language import resolution
    python.py          #   dotted imports via module index: __init__.py
                       #   barrels, src/ + monorepo packages/*/src, namespace pkgs
    typescript.py      #   multi-ext probe, tsconfig aliases
    go.py              #   go.mod module path stripping
    rust.py            #   crate::/self::/super::, mod.rs probing
    cpp.py             #   compile_commands.json include paths
    kotlin.py          #   package-to-directory mapping
    ruby.py            #   require/require_relative resolution
    csharp.py / dotnet/ #  namespace-based + MSBuild project graph
    swift.py           #   module import resolution
    scala.py           #   package-to-directory mapping
    php.py             #   namespace/PSR-4 resolution
    generic.py         #   stem-matching fallback
  framework_edges/     # Framework convention edges (one module per framework + base.py)
                       #   __init__.py re-exports add_framework_edges; iterates FrameworkHandler list
                       #   django/fastapi/flask/aspnet/rails/laravel/spring/express/go/rust/typo3
                       #   + pytest_edges (conftest); base.py = read_text, _add_edge_if_new, name→file maps
  dynamic_hints/       # Per-language dynamic-edge extractors
    base.py            #   DynamicHintExtractor + DynamicEdge
    registry.py        #   HintRegistry
    django.py  pytest_hints.py  python_imports.py  node.py  dotnet.py
    spring.py  ruby.py  php.py  scala.py  swift.py  c.py  luau.py  go.py
  parser.py            # ASTParser (language-agnostic orchestration)
  graph.py             # GraphBuilder (import/call/heritage resolution)

analysis/
  dead_code/           # Dead code detection (Phase 1 split)
    __init__.py        #   re-exports DeadCodeAnalyzer + dataclasses
    analyzer.py        #   DeadCodeAnalyzer class + four detection passes
    models.py          #   DeadCodeKind, DeadCodeFindingData, DeadCodeReport
    constants.py       #   never-flag globs, framework decorators, fixtures
    dynamic_markers.py #   per-language source-text dynamic markers
```

Adding a new language requires zero changes to `parser.py`, `graph.py`,
`traverser.py`, or any analysis core file. New language work consists of
adding one file to each per-language subpackage and registering it in the
relevant `__init__.py` dispatcher / dict.

---

## Workspace contract extraction

In **workspace mode** (multiple repos indexed together), repowise links
service-to-service API contracts — HTTP routes and gRPC services — so a
provider endpoint in one repo connects to its consumers in another. The
extractors live in `core/workspace/extractors/` and follow the same
dialect-plugin shape as the rest of the language pipeline: the orchestrator
owns only the file walk, and each framework / client library is an
independent module registered in a tuple.

```
workspace/extractors/
  base.py            # iter_source_files walk + ScanContext (shared by all)
  langs.py           # registry-derived extension sets (JS_TS, PYTHON, RUST, …)
  http/
    dialect.py       #   HttpDialect protocol + build_provider/consumer_contract
    paths.py         #   normalize_http_path + URL helpers
    express.py  fastapi.py  spring.py  laravel.py  go.py  aspnet.py   # providers
    js_clients.py  python_clients.py  csharp_http.py                  # consumers
    __init__.py      #   HttpExtractor + PROVIDER_DIALECTS / CONSUMER_DIALECTS
  grpc/
    dialect.py       #   GrpcDialect protocol + make_grpc_contract
    proto.py  go.py  java.py  python.py  typescript.py  csharp.py
    __init__.py      #   GrpcExtractor + DIALECTS
```

A dialect declares the file extensions it understands (via `langs.py`) and
turns regex matches into `Contract`s through the shared builders, so every
dialect emits identically-shaped providers/consumers and the path-normalization
rules live in one place. Framework-specific quirks stay local to the dialect —
Spring/ASP.NET class-prefix stitching, Go `HandleFunc` → `*` method, fetch
GET/method de-duplication.

**Adding a framework or client** means dropping one module into `http/` or
`grpc/` and appending its dialect to the relevant registry tuple
(`PROVIDER_DIALECTS`, `CONSUMER_DIALECTS`, or `DIALECTS`) — no orchestrator
edits. The current coverage:

| Contract | Providers | Consumers |
|----------|-----------|-----------|
| **HTTP** | Express, FastAPI, Spring, Laravel, Go (gin/echo/chi/net-http), ASP.NET (attribute + minimal), Rust (Axum routes, Actix/Rocket attribute macros) | `fetch` / `axios` (JS/TS), `requests` / `httpx` (Python), `HttpClient` (C#) |
| **gRPC** | `.proto` IDL, Go, Java, Python, NestJS (`@GrpcMethod`), C# (gRPC-dotnet) | Go, Java, Python, C# |

---

## Roadmap

| Language | Target Tier | Status |
|----------|------------|--------|
| Dart | Good | Lightweight tier shipped; AST upgrade planned — `tree-sitter-dart` available |
| Elixir | Good | Lightweight tier shipped; AST upgrade planned — `tree-sitter-elixir` available |
| F# | Good | Lightweight tier shipped; AST upgrade planned — `tree-sitter-f-sharp` available |
