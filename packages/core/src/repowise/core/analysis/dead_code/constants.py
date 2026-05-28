"""Static configuration for dead-code detection.

These tuples / frozensets shape what the analyzer treats as "always
alive" (framework decorators, never-flag path globs) and where to skip
entirely (test fixture directories, non-code languages).
"""

from __future__ import annotations

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY


# Non-code languages that should never be flagged as dead code.
# Derived from the centralised LanguageRegistry — passthrough config/infra
# languages plus "unknown".
_NON_CODE_LANGUAGES: frozenset[str] = frozenset(
    spec.tag
    for spec in _LANG_REGISTRY.all_specs()
    if spec.is_passthrough and (not spec.is_code or spec.is_infra) and spec.tag != "openapi"
) | {"unknown"}

# Patterns that should never be flagged as dead.
_NEVER_FLAG_PATTERNS: tuple[str, ...] = (
    "*__init__.py",
    "*__main__.py",
    "*conftest.py",
    "*alembic/env.py",
    # Alembic migration scripts live in <root>/versions/<rev>_<slug>.py and
    # are loaded reflectively by Alembic at runtime from script_location in
    # alembic.ini — they have no static importer by design. The legacy
    # ``*migrations*`` glob only matches paths that literally contain the
    # token ``migrations``; many Alembic setups use ``alembic/versions/``.
    "*/alembic/versions/*.py",
    "*manage.py",
    "*wsgi.py",
    "*asgi.py",
    "*migrations*",
    "*schema*",
    "*seed*",
    "*.d.ts",
    "*setup.py",
    "*setup.cfg",
    "*next.config.*",
    "*vite.config.*",
    "*tailwind.config.*",
    "*postcss.config.*",
    "*jest.config.*",
    "*vitest.config.*",
    # Next.js / Remix / SvelteKit framework route files — loaded by the
    # framework at runtime, never imported via module imports.
    "*/page.tsx",
    "*/page.ts",
    "*/page.jsx",
    "*/page.js",
    "*/layout.tsx",
    "*/layout.ts",
    "*/route.tsx",
    "*/route.ts",
    "*/loading.tsx",
    "*/error.tsx",
    "*/not-found.tsx",
    "*/template.tsx",
    "*/default.tsx",
    # Nuxt route pages
    "*/pages/*.vue",
    # ---- .NET / C# conventions --------------------------------------
    # Implicit / generated / framework-loaded files that have no
    # static importers by design.
    "*GlobalUsings.cs",          # global usings — file-implicit, never imported by symbol
    "*.xaml.cs",                 # XAML code-behind, wired by the source generator
    "*.xaml",
    "*.razor",
    "*.razor.cs",                # Blazor code-behind
    "*.razor.js",                # Blazor JS interop side-files
    "*.cshtml",
    "*.cshtml.cs",
    "*.designer.cs",             # Roslyn designer
    "*Designer.cs",
    "*.g.cs",                    # Roslyn-generated
    "*.g.i.cs",
    "*.AssemblyInfo.cs",
    "*MauiProgram.cs",           # MAUI app entry — invoked by host, not imported
    "*App.xaml.cs",
    "*AppShell.xaml.cs",
    # Aspire / ServiceDefaults host wiring is consumed by AppHost project graph,
    # not by C# `using` directives.
    "*AppHost*.cs",
    "*ServiceDefaults*.cs",
    # Integration events + EF entity configurations are loaded reflectively
    # by event-bus subscribers and EF model builder respectively.
    "*IntegrationEvent.cs",
    "*IntegrationEvents/Events/*.cs",
    "*EntityConfigurations/*.cs",
    "*EntityTypeConfiguration.cs",
    # gRPC generated artifacts.
    "*.pb.cs",
    "*Grpc.cs",
    # Minimal-API endpoint modules — ASP.NET Core convention. These
    # static classes expose extension methods like ``MapCatalogApi``
    # that are wired by ``app.MapCatalogApi()`` in ``Program.cs``. The
    # static call doesn't currently land in the import graph, so without
    # an explicit pass these read as orphaned every time.
    "*/Apis/*.cs",
    "*/Endpoints/*.cs",
    "*/Routes/*.cs",
    # ---- Generic .NET / Win32 conventions ----------------------------
    # Source-generator output directories. Many SDKs (CommunityToolkit
    # MVVM, AOT, EF Core compiled-models, gRPC) emit generated files
    # into a `Generated/` sibling next to the source. They get wired in
    # at build time, never imported by name.
    "*/Generated/*.cs",
    "*/generated/*.cs",
    # Win32 P/Invoke surfaces. NativeMethods / SafeNativeMethods are a
    # decades-old .NET FX convention; they are reached only via
    # `[DllImport]`-mediated calls, never via a `using` directive that
    # names the type.
    "*NativeMethods.cs",
    "*SafeNativeMethods.cs",
    "*UnsafeNativeMethods.cs",
    # ETW / EventSource event-class folders. The runtime reflects on
    # these at registration time; static graph rarely sees the import.
    "*/Telemetry/Events/*.cs",
    "*/Diagnostics/Events/*.cs",
    # XAML resource dictionaries and merged styles. WPF / WinUI load
    # these via `<ResourceDictionary Source="..."/>` not `using`.
    "*/Themes/*.xaml",
    "*/Styles/*.xaml",
    "*/Resources/*.xaml",
    # ---- Test infrastructure conventions -----------------------------
    # Test classes are loaded by the test runner via reflection on
    # ``[Test]`` / ``[TestMethod]`` / ``[Fact]`` attributes — they
    # never appear in a `using` import that names the class. Match
    # both the file location *and* the standard suffix patterns so we
    # catch tests dropped at arbitrary paths.
    "*Tests/*.cs",
    "*.Tests/*.cs",
    "*UnitTests/*.cs",
    "*.UnitTests/*.cs",
    "*IntegrationTests/*.cs",
    "*.IntegrationTests/*.cs",
    "*FuzzTests/*.cs",
    "*.FuzzTests/*.cs",
    "*UITests/*.cs",
    "*.UITests/*.cs",
    "*UITest/*.cs",
    "*UITestAutomation/*.cs",
    # Singular forms used by PowerToys / Wox / etc.
    "*UnitTest/*.cs",
    "*.UnitTest/*.cs",
    "*.Test/*.cs",
    "*/Wox.Test/*.cs",
    # MSTest convention of ``UnitTests-<Subject>`` / ``UITest-<Subject>``
    # directories (PowerToys preview handler / per-module test projects).
    "*/UnitTests-*/*.cs",
    "*/UITest-*/*.cs",
    "*/UnitTests-*/*.cpp",
    "*/UnitTests-*/*.h",
    "*/unittests/*.cpp",
    "*/unittests/*.h",
    # File-suffix conventions for tests dropped outside a test project.
    "*Tests.cs",
    "*UnitTests.cs",
    "*Test.cs",
    "*Test.cpp",
    "*Tests.cpp",
    # ---- Precompiled headers and COM ClassFactory shims --------------
    # ``pch.h`` / ``pch.cpp`` (and the older ``stdafx.*``) are MSVC
    # precompiled-header anchors — referenced by build settings, never
    # by user code. ``*ClassFactory.cpp`` is the COM ``IClassFactory``
    # implementation; the type is registered via DllGetClassObject and
    # activated by Windows, so it has no static caller.
    "*/pch.h",
    "*/pch.cpp",
    "*/pch.cc",
    "*/PrecompiledHeader.cpp",
    "*/PrecompiledHeader.cc",
    "*/stdafx.h",
    "*/stdafx.cpp",
    "*ClassFactory.cpp",
    "*ClassFactory.h",
    # ---- C / C++ conventions ---------------------------------------------
    # ``fnmatch`` ``*`` spans ``/`` so a leading ``*`` matches both nested
    # module paths and repo-root layouts.
    # Apps / demos / examples / tools / benchmarks — every file under these
    # trees compiles to a standalone binary by CMake/Bazel ``add_executable``
    # / ``cc_binary``. They have no static importer by design.
    "*/apps/*.cc",
    "*/apps/*.cpp",
    "*/apps/*.cxx",
    "*/apps/*.c",
    "*/apps/*.h",
    "*/apps/*.hpp",
    "*/apps/**/*.cc",
    "*/apps/**/*.cpp",
    "*/apps/**/*.cxx",
    "*/apps/**/*.c",
    "*/apps/**/*.h",
    "*/apps/**/*.hpp",
    "apps/*.cc",
    "apps/*.cpp",
    "apps/*.c",
    "apps/**/*.cc",
    "apps/**/*.cpp",
    "apps/**/*.cxx",
    "apps/**/*.c",
    "apps/**/*.h",
    "apps/**/*.hpp",
    "*/demos/*.cc",
    "*/demos/*.cpp",
    "*/demos/*.cxx",
    "*/demos/*.c",
    "*/demos/**/*.cc",
    "*/demos/**/*.cpp",
    "*/demos/**/*.cxx",
    "*/demos/**/*.c",
    "demos/*.cc",
    "demos/*.cpp",
    "demos/*.c",
    "demos/**/*.cc",
    "demos/**/*.cpp",
    "demos/**/*.cxx",
    "demos/**/*.c",
    "*/examples/*.cc",
    "*/examples/*.cpp",
    "*/examples/*.cxx",
    "*/examples/*.c",
    "*/examples/*.h",
    "*/examples/**/*.cc",
    "*/examples/**/*.cpp",
    "*/examples/**/*.cxx",
    "*/examples/**/*.c",
    "*/examples/**/*.h",
    "*/examples/**/*.hpp",
    "examples/*.cc",
    "examples/*.cpp",
    "examples/**/*.cc",
    "examples/**/*.cpp",
    "examples/**/*.cxx",
    "examples/**/*.c",
    "examples/**/*.h",
    "*/sample/*.cc",
    "*/sample/*.cpp",
    "*/samples/*.cc",
    "*/samples/*.cpp",
    "*/samples/**/*.cc",
    "*/samples/**/*.cpp",
    "samples/**/*.cc",
    "samples/**/*.cpp",
    "*/benchmarks/*.cc",
    "*/benchmarks/*.cpp",
    "*/benchmarks/*.cxx",
    "*/benchmarks/*.c",
    "*/benchmarks/**/*.cc",
    "*/benchmarks/**/*.cpp",
    "benchmarks/*.cc",
    "benchmarks/*.cpp",
    "benchmarks/*.c",
    "benchmarks/**/*.cc",
    "benchmarks/**/*.cpp",
    "*/bench/*.cc",
    "*/bench/*.cpp",
    "bench/*.cc",
    "bench/*.cpp",
    # C/C++ tool directories (``leveldbutil.cc`` / ``db_repair.cc`` shape).
    "*/tools/*.cc",
    "*/tools/*.cpp",
    "*/tools/**/*.cc",
    "*/tools/**/*.cpp",
    "tools/*.cc",
    "tools/*.cpp",
    "tools/**/*.cc",
    "tools/**/*.cpp",
    # C/C++ test trees — every framework (GoogleTest / Catch2 / Boost.Test
    # / doctest / Google Benchmark / libFuzzer) discovers tests by glob, not
    # by static import.
    "*/tests/**/*_test.cc",
    "*/tests/**/*_test.cpp",
    "*/tests/**/*_test.cxx",
    "*/tests/**/*_test.c",
    "*/tests/**/*_unittest.cc",
    "*/tests/**/*_unittest.cpp",
    "*/tests/**/*_perftest.cc",
    "*/tests/**/*_perftest.cpp",
    "*/tests/**/*_perf.cc",
    "*/tests/**/*_perf.cpp",
    "*/tests/**/*_benchmark.cc",
    "*/tests/**/*_benchmark.cpp",
    "*/tests/**/*_benchmarks.cc",
    "*/tests/**/*_benchmarks.cpp",
    "*/tests/**/*_fuzz.cc",
    "*/tests/**/*_fuzz.cpp",
    "*/tests/perf/*.cc",
    "*/tests/perf/*.cpp",
    "*/tests/unit/*.cc",
    "*/tests/unit/*.cpp",
    "*/tests/unit/*.h",
    "*/tests/unit/*.hpp",
    "*/tests/fuzz/*.cc",
    "*/tests/fuzz/*.cpp",
    "*/tests/integration/*.cc",
    "*/tests/integration/*.cpp",
    "*/tests/manual/*",
    # Broad test-tree coverage for project layouts that don't follow the
    # ``*_test.{cc,cpp}`` suffix convention. nlohmann/json uses ``tests/src/
    # unit-*.cpp``, ``tests/abi/diag/diag_off.cpp``, and ``tests/cmake_*/
    # project/*.cpp`` — none match the suffix globs above. Anything inside a
    # repo-rooted ``tests/`` tree is framework-discovered by the build
    # system, not statically imported.
    "*/tests/**/*.cc",
    "*/tests/**/*.cpp",
    "*/tests/**/*.cxx",
    "*/tests/**/*.h",
    "*/tests/**/*.hpp",
    "tests/**/*_test.cc",
    "tests/**/*_test.cpp",
    "tests/**/*_unittest.cc",
    "tests/**/*_unittest.cpp",
    "tests/**/*_perf.cc",
    "tests/**/*_perf.cpp",
    "tests/**/*_fuzz.cc",
    "tests/**/*_fuzz.cpp",
    "tests/perf/*.cc",
    "tests/perf/*.cpp",
    "tests/unit/*.cc",
    "tests/unit/*.cpp",
    "tests/unit/*.h",
    "tests/fuzz/*.cc",
    "tests/fuzz/*.cpp",
    "tests/manual/*",
    # Repo-rooted broad coverage matching the ``*/tests/**`` block above —
    # nlohmann/json's tree (``tests/src/unit-*.cpp``, ``tests/abi/...``)
    # lives under the repo root with no leading prefix.
    "tests/**/*.cc",
    "tests/**/*.cpp",
    "tests/**/*.cxx",
    "tests/**/*.h",
    "tests/**/*.hpp",
    # File-suffix conventions for tests dropped outside a standard test
    # directory (GoogleTest / Google Benchmark / libFuzzer / Catch2).
    "*_test.cc",
    "*_test.cpp",
    "*_test.cxx",
    "*_test.h",
    "*_unittest.cc",
    "*_unittest.cpp",
    "*_perftest.cc",
    "*_perftest.cpp",
    "*_perf.cc",
    "*_perf.cpp",
    "*_benchmark.cc",
    "*_benchmark.cpp",
    "*_benchmarks.cc",
    "*_benchmarks.cpp",
    "*_fuzz.cc",
    "*_fuzz.cpp",
    # Conventional port / example skeleton headers — projects ship them
    # to document a portability layer; never actually built.
    "*/port_example.h",
    "*/port/port_example.h",
    "*_example.h",
    "*_example.hpp",
    "*_example.cc",
    # Generated source roots (CMake build dirs, autoconf / out-of-tree
    # builds). The walker normally skips them but, when they leak in,
    # they're never importers.
    "*/build/**",
    "build/**",
    "*/cmake-build-*/**",
    "cmake-build-*/**",
    "*/_deps/**",
    "_deps/**",
    "*/out/build/**",
    "*/out/Debug/**",
    "*/out/Release/**",
    # Generated source-file patterns. Wired in at build time, no static
    # importer; the analyzer should silence them universally.
    "moc_*.cpp",                # Qt MOC
    "moc_*.cc",
    "ui_*.h",                   # Qt UIC
    "qrc_*.cpp",                # Qt RCC
    "qrc_*.cc",
    "*.moc",                    # inline MOC includes
    "*.pb.cc",                  # protoc generated
    "*.pb.h",
    "*.pb-c.c",                 # protobuf-c
    "*.pb-c.h",
    "*.grpc.pb.cc",             # protoc-gen-grpc
    "*.grpc.pb.h",
    "*.capnp.c++",              # Cap'n Proto
    "*.capnp.h",
    "*.flatbuffers.h",
    "*_generated.h",            # FlatBuffers convention
    "*.tab.c",                  # Bison / Yacc
    "*.tab.h",
    "*.yy.c",                   # Flex / Lex
    "*_lex.cc",
    "*_wrap.cxx",               # SWIG
    "*_wrap.cpp",
    "*.cython.cpp",             # Cython
    # Vendored / third-party roots. The existing ``vendor`` / ``third_party``
    # / ``deps`` globs only cover ``.c`` / ``.h``; extend them to the full
    # C++ extension set, and add the additional vendor conventions
    # ``external/`` / ``extern/`` / ``contrib/`` / ``submodules/``.
    "*/vendor/**/*.cc",
    "*/vendor/**/*.cpp",
    "*/vendor/**/*.cxx",
    "*/vendor/**/*.hpp",
    "*/vendor/**/*.hxx",
    "vendor/**/*.cc",
    "vendor/**/*.cpp",
    "vendor/**/*.cxx",
    "vendor/**/*.hpp",
    "*/third_party/**/*.cc",
    "*/third_party/**/*.cpp",
    "*/third_party/**/*.cxx",
    "*/third_party/**/*.hpp",
    "*/third_party/**/*.hxx",
    "third_party/**/*.cc",
    "third_party/**/*.cpp",
    "third_party/**/*.cxx",
    "third_party/**/*.hpp",
    "*/deps/**/*.cc",
    "*/deps/**/*.cpp",
    "*/deps/**/*.cxx",
    "*/deps/**/*.hpp",
    "deps/**/*.cc",
    "deps/**/*.cpp",
    "deps/**/*.cxx",
    "deps/**/*.hpp",
    "*/external/**/*.c",
    "*/external/**/*.h",
    "*/external/**/*.cc",
    "*/external/**/*.cpp",
    "*/external/**/*.cxx",
    "*/external/**/*.hpp",
    "*/external/**/*.hxx",
    "external/**/*.c",
    "external/**/*.h",
    "external/**/*.cc",
    "external/**/*.cpp",
    "external/**/*.cxx",
    "external/**/*.hpp",
    "*/extern/**/*.c",
    "*/extern/**/*.h",
    "*/extern/**/*.cc",
    "*/extern/**/*.cpp",
    "*/extern/**/*.hpp",
    "extern/**/*.c",
    "extern/**/*.h",
    "extern/**/*.cc",
    "extern/**/*.cpp",
    "*/contrib/**/*.c",
    "*/contrib/**/*.h",
    "*/contrib/**/*.cc",
    "*/contrib/**/*.cpp",
    "*/contrib/**/*.cxx",
    "*/contrib/**/*.hpp",
    "contrib/**/*.c",
    "contrib/**/*.h",
    "contrib/**/*.cc",
    "contrib/**/*.cpp",
    "*/submodules/**",
    "submodules/**",
    "*/.deps/**",
    # ---- Rust / Cargo conventions ----------------------------------------
    # Build scripts (executed by Cargo at compile time, never imported)
    "**/build.rs",
    "build.rs",
    # Examples (run via `cargo run --example <name>`)
    "**/examples/*.rs",
    "**/examples/**/*.rs",
    "examples/*.rs",
    "examples/**/*.rs",
    # Benchmarks (run via `cargo bench`)
    "**/benches/*.rs",
    "**/benches/**/*.rs",
    "benches/*.rs",
    "benches/**/*.rs",
    # Integration tests (run via `cargo test`)
    # Note: fnmatch **/ requires at least one leading directory component, so
    # we also add bare patterns for repos where tests/ sits at the root.
    "**/tests/*.rs",
    "**/tests/**/*.rs",
    "tests/*.rs",
    "tests/**/*.rs",
    # Binary targets (separate executables in a crate)
    "**/src/bin/*.rs",
    "**/src/bin/**/*.rs",
    # Fuzz targets (various layouts: fuzz/src/, fuzz_targets/, fuzz/fuzz_targets/)
    "**/fuzz/src/**/*.rs",
    "**/fuzz_targets/**/*.rs",
    "**/fuzz/fuzz_targets/**/*.rs",
    "fuzz/src/**/*.rs",
    "fuzz_targets/**/*.rs",
    "fuzz/fuzz_targets/**/*.rs",
    # Derive macro crates (proc-macro, consumed at compile time)
    "**/derive/src/*.rs",
    # Generated code
    "**/generated/**/*.rs",
    # Protocol buffer generated code
    "**/proto/**/*.rs",
    # ---- Go conventions --------------------------------------------------
    # ``fnmatch`` does not treat ``/`` specially, so a leading ``*`` already
    # spans directory separators; the ``*/`` + bare pairs below match both
    # nested and repo-root locations.
    # Test files are compiled and run by ``go test`` via the runner, never
    # imported by other packages.
    "*_test.go",
    # ``package main`` entry points — invoked by the Go toolchain / OS, never
    # imported. Covers ``cmd/<name>/main.go`` and a repo-root ``main.go``.
    "*/main.go",
    "main.go",
    # Package documentation stubs (the ``doc.go`` / ``docs.go`` convention):
    # a file holding only the package-level doc comment, frequently the sole
    # file in an aggregating directory, so it has no importer by design.
    "*/doc.go",
    "doc.go",
    "*/docs.go",
    "docs.go",
    # Mage build files (``//go:build mage``, ``package main``) — run by the
    # ``mage`` tool, excluded from normal builds, never imported.
    "*/magefile.go",
    "magefile.go",
    # Generated code: stringer (``*_string.go``), protobuf (``*.pb.go``),
    # gRPC (``*_grpc.pb.go``), go-bindata (``*bindata.go``), and the
    # Kubernetes ``zz_generated*`` / generic ``*_gen.go`` / ``*.gen.go``
    # conventions. Wired in at build time, no static importer.
    "*.gen.go",
    "*_gen.go",
    "*.pb.go",
    "*_string.go",
    "*zz_generated*.go",
    "*bindata.go",
    # ---- JavaScript conventions ------------------------------------------
    # ``fnmatch`` ``*`` spans ``/`` (see the Go note above), so leading-``*``
    # suffix globs match nested asset paths too.
    # esbuild / rollup / webpack bundle outputs and minified artifacts are
    # build products served to the browser, not module-imported by name.
    "*.bundle.js",
    "*.min.js",
    "*.bundle.mjs",
    # LiveReload ships a generated browser-global script set (IIFE /
    # ``window.LiveReload =``) under ``livereload/gen/`` plus the
    # ``livereload.js`` shim — loaded via a ``<script>`` tag, never imported.
    "*/livereload/gen/*",
    "livereload/gen/*",
    "*/livereload/livereload.js",
    "livereload/livereload.js",
    # Hugo embeds a WASM toolchain whose JS glue under ``internal/warpc/js``
    # is shipped as a bundle and loaded by the Go side via ``go:embed``,
    # never by a JS importer.
    "*wasm_exec.js",
    # ---- Vendored / third-party C ----------------------------------------
    # A vendored C library (``deps/`` / ``vendor/`` / ``third_party/``) is a
    # self-contained unit: its structs/typedefs and functions are used within
    # the dependency's own translation units, not exported to the host repo
    # by a statement the graph sees. Flagging its internals as dead is noise —
    # the dependency is maintained upstream, not here. (Hugo's
    # ``internal/warpc/deps/parson`` JSON lib is the canonical case.)
    "*/deps/**/*.c",
    "*/deps/**/*.h",
    "deps/**/*.c",
    "deps/**/*.h",
    "*/vendor/**/*.c",
    "*/vendor/**/*.h",
    "vendor/**/*.c",
    "vendor/**/*.h",
    "*/third_party/**/*.c",
    "*/third_party/**/*.h",
    "third_party/**/*.c",
    "third_party/**/*.h",
    # ---- JavaScript / TypeScript conventions -----------------------------
    # ``fnmatch`` treats ``*`` as "match anything including ``/``", so a
    # leading ``*`` is enough to span both repo-root and nested paths.
    # Test files — discovered by the runner via filename glob (vitest,
    # jest, mocha, playwright, cypress), never imported by sibling source.
    "*.test.ts",
    "*.test.tsx",
    "*.test.js",
    "*.test.jsx",
    "*.test.mjs",
    "*.test.cjs",
    "*.spec.ts",
    "*.spec.tsx",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.mjs",
    "*/__tests__/*",
    "*/__mocks__/*",
    # Storybook stories — loaded by Storybook indexer via glob.
    "*.stories.ts",
    "*.stories.tsx",
    "*.stories.js",
    "*.stories.jsx",
    "*.stories.mdx",
    # Benchmarks — invoked by vitest/tinybench/bench scripts, not imported.
    "*.bench.ts",
    "*.bench.tsx",
    "*.bench.js",
    "*.bench.mjs",
    # Vitest / Playwright / Cypress config and workspace files.
    "*vitest.workspace.*",
    "*vitest.shims.*",
    "*vitest.root.*",
    "*playwright.config.*",
    "*cypress.config.*",
    "*rollup.config.*",
    "*esbuild.config.*",
    "*tsup.config.*",
    "*.config.mts",
    "*.config.cts",
    # Codegen / generated artifacts.
    "*.gen.ts",
    "*.gen.tsx",
    "*_generated.ts",
    "*_generated.tsx",
    "*.codegen.ts",
    "*/generated/*.ts",
    "*/generated/*.tsx",
    "*/generated/*.js",
    "*/__generated__/*",
    # Next.js app router convention files beyond page/layout/route already
    # covered above — loaded by the framework, no static importer. The
    # ``*/foo.ts`` form ensures the literal basename is required (so
    # ``mymiddleware.ts`` isn't accidentally exempt).
    "*/instrumentation.ts",
    "*/instrumentation-client.ts",
    "*/middleware.ts",
    "*/middleware.js",
    "*/global-error.tsx",
    "*/global-error.ts",
    "*/forbidden.tsx",
    "*/unauthorized.tsx",
    "*/sitemap.ts",
    "*/sitemap.tsx",
    "*/robots.ts",
    "*/robots.tsx",
    "*/manifest.ts",
    "*/manifest.tsx",
    "*/icon.tsx",
    "*/icon.ts",
    "*/apple-icon.tsx",
    "*/opengraph-image.tsx",
    "*/opengraph-image.ts",
    "*/twitter-image.tsx",
    "*/twitter-image.ts",
    # Repo-root variants (no leading directory) for monorepos where the
    # app lives at root.
    "instrumentation.ts",
    "middleware.ts",
    "sitemap.ts",
    "robots.ts",
    # Remix root/entry files — invoked by the framework runtime.
    "*/entry.client.ts",
    "*/entry.client.tsx",
    "*/entry.server.ts",
    "*/entry.server.tsx",
    "*/next-env.d.ts",
    "next-env.d.ts",
    # SvelteKit + Nuxt convention files.
    "*/+page.svelte",
    "*/+page.ts",
    "*/+page.server.ts",
    "*/+layout.svelte",
    "*/+layout.ts",
    "*/+layout.server.ts",
    "*/+server.ts",
    "*/+error.svelte",
    # ESM declaration outputs in dist trees.
    "*/dist/*.d.ts",
    # ---- JVM (Java + Kotlin) conventions ---------------------------------
    # JPMS module descriptors and package-info files declare no importable
    # symbols by design — they exist to carry module/package metadata that
    # the JVM consumes via reflection at link/load time.
    "*/module-info.java",
    "module-info.java",
    "*/package-info.java",
    "package-info.java",
    # Gradle / Maven test source sets. ``fnmatch`` ``*`` spans ``/`` so a
    # leading ``*`` matches both nested module trees and repo-root layouts.
    # The ``*Test`` and ``*Tests`` globs catch project-specific source-set
    # names (``apacheTest``, ``eclipseTest``, ``frayTest``, ``intTest``,
    # ``functionalTest``, ``smokeTest`` …) without an explicit allowlist.
    "*/src/test/java/*",
    "*/src/test/kotlin/*",
    "*/src/integrationTest/*",
    "*/src/it/*",
    "*/src/intTest/*",
    "*/src/e2eTest/*",
    "*/src/functionalTest/*",
    "*/src/smokeTest/*",
    "*/src/acceptanceTest/*",
    "*/src/jmh/*",
    "*/src/perfTest/*",
    "*/src/*Test/java/*",
    "*/src/*Tests/java/*",
    "*/src/*Test/kotlin/*",
    "*/src/*Tests/kotlin/*",
    # Repo-root variants (single-module project).
    "src/test/java/*",
    "src/test/kotlin/*",
    "src/integrationTest/*",
    "src/intTest/*",
    "src/*Test/java/*",
    "src/*Tests/java/*",
    # File-suffix conventions for tests dropped outside a standard source
    # set (TestNG, JUnit, Spock, ArchUnit, pact-jvm, jqwik).
    "*Test.java",
    "*Tests.java",
    "*IT.java",
    "*ITCase.java",
    "*TestCase.java",
    "*FrayTest.java",
    "*Benchmark.java",
    "*Spec.java",
    "*Specification.java",
    "*Test.kt",
    "*Tests.kt",
    "*IT.kt",
    "*Spec.kt",
    "*Specification.kt",
    # Generated source roots emitted by Gradle / Maven / KAPT / KSP /
    # annotation-processors / GraalVM AOT. Wired in at build time by the
    # JVM toolchain, never imported by static source.
    "*/build/generated/*",
    "*/build/generated-src/*",
    "*/target/generated-sources/*",
    "*/target/generated-test-sources/*",
    "*/build/generated/source/kapt/*",
    "*/build/generated/source/kaptKotlin/*",
    "*/build/generated/source/ksp/*",
    "*/build/generated/aotSources/*",
    # Generated-name suffixes that have unambiguous tool ownership.
    "*_Generated.java",
    "*$Generated.java",
    "*MapperImpl.java",            # MapStruct compile-time impl
    "*Dagger*.java",
    "*AutoValue_*.java",
    "*ServiceGrpc.java",
    "*OuterClass.java",            # protoc generated outer class
    "*$WrapperImpl.java",
)

# Decorator patterns that indicate framework usage (route handlers, fixtures, etc.)
_FRAMEWORK_DECORATORS: tuple[str, ...] = (
    "pytest.fixture",
    "pytest.mark",
    # Flask
    "app.route",
    "blueprint.route",
    "bp.route",
    # FastAPI
    "router.get",
    "router.post",
    "router.put",
    "router.delete",
    "router.patch",
    "router.head",
    "router.options",
    "router.websocket",
    "app.get",
    "app.post",
    "app.put",
    "app.delete",
    "app.patch",
    "app.head",
    "app.options",
    "app.websocket",
    "app.middleware",
    "app.exception_handler",
    # asynccontextmanager / contextmanager — used as values
    # (e.g. FastAPI(lifespan=...)) rather than imported by name.
    "asynccontextmanager",
    "contextmanager",
    "contextlib.asynccontextmanager",
    "contextlib.contextmanager",
    # Django
    "admin.register",
    "receiver",
    # Celery / RQ task registration
    "app.task",
    "celery.task",
    "shared_task",
    # Click CLI commands — registered with the parent group/command.
    "click.command",
    "click.group",
    # Typer — same shape.
    "typer.command",
    "typer.callback",
    # ---- JVM: Spring / Jakarta / Quarkus / Micronaut stereotypes ----
    # Bare-name match against the stripped ``@Foo`` form. A class or
    # method bearing one of these is wired into the container / route
    # table / event bus by reflection — never imported by source name.
    "Component",
    "Service",
    "Repository",
    "Controller",
    "RestController",
    "Configuration",
    "ControllerAdvice",
    "RestControllerAdvice",
    "Mapper",                      # MapStruct + MyBatis
    "SpringBootApplication",
    "SpringBootConfiguration",
    "EnableAutoConfiguration",
    "Configurable",
    "Endpoint",
    "RestControllerEndpoint",
    "WebMvcTest",
    "DataJpaTest",
    "Entity",
    "MappedSuperclass",
    "Embeddable",
    "Converter",                   # JPA AttributeConverter
    "QuarkusMain",
    "QuarkusTest",
    "QuarkusIntegrationTest",
    "NativeImageTest",
    "MicronautApplication",
    "MicronautTest",
    "Path",                        # JAX-RS
    "Provider",
    "WebServlet",
    "WebFilter",
    "WebListener",
    "ApplicationScoped",
    "RequestScoped",
    "SessionScoped",
    "Singleton",
    "Stateless",
    "Stateful",
    "Dependent",
    "Factory",
    "Bean",
    # ---- JVM: lifecycle / event / scheduling / messaging callbacks --
    "PostConstruct",
    "PreDestroy",
    "EventListener",
    "TransactionalEventListener",
    "Scheduled",
    "Schedule",                    # Quarkus
    "JmsListener",
    "KafkaListener",
    "RabbitListener",
    "SqsListener",
    "StreamListener",
    "Incoming",
    "Outgoing",
    "OnOpen",
    "OnClose",
    "OnMessage",
    "OnError",
    "PactTestFor",
    "Pact",
    # ---- JVM: test method markers -----------------------------------
    "Test",
    "ParameterizedTest",
    "RepeatedTest",
    "TestFactory",
    "TestTemplate",
    "BeforeAll",
    "AfterAll",
    "BeforeEach",
    "AfterEach",
    "Before",
    "After",
    "BeforeClass",
    "AfterClass",
    "DataProvider",
    "ArchTest",
    "Container",                   # Testcontainers
    "DynamicTest",
    "JsonCreator",                 # Jackson factory method — reflectively invoked
    "JsonProperty",
    "Mojo",                        # Maven plugin entry
    "Goal",
    "RegisterForReflection",
    # ---- JVM: routing / HTTP-verb annotations -----------------------
    # A method bearing one of these is a route handler — invoked by the
    # framework dispatcher, not by source. Treated as an entry point.
    "RequestMapping",
    "GetMapping",
    "PostMapping",
    "PutMapping",
    "DeleteMapping",
    "PatchMapping",
    "MessageMapping",
    "SubscribeMapping",
    "ExceptionHandler",
    "InitBinder",
    "ModelAttribute",
    "GET",                         # JAX-RS / Quarkus / Micronaut
    "POST",
    "PUT",
    "DELETE",
    "PATCH",
    "HEAD",
    "OPTIONS",
    "Route",
)

# Decorator *suffixes* that indicate framework registration regardless of
# the receiver name. Many Click/Typer codebases register subcommands on a
# locally-named Group instance, e.g.::
#
#     decision_group = click.Group(...)
#
#     @decision_group.command("add")
#     def decision_add(): ...
#
# The decorator is captured as ``decision_group.command`` — its prefix is
# project-local, but its trailing attribute (``.command``) is a strong
# signal that the wrapped function is registered with a framework
# dispatcher and not called by name. Matching the suffix catches every
# Click ``Group`` / Typer ``Typer`` / aiogram ``Dispatcher`` / aiohttp
# ``RouteTableDef`` etc. without hard-coding receiver names.
_FRAMEWORK_DECORATOR_SUFFIXES: tuple[str, ...] = (
    ".command",
    ".group",
    ".callback",
)



# Default dynamic patterns (plugins, handlers, etc.)
_DEFAULT_DYNAMIC_PATTERNS: tuple[str, ...] = (
    "*Plugin",
    "*Handler",
    "*Adapter",
    "*Middleware",
    "*Mixin",
    "*Command",
    "register_*",
    "on_*",
    # Common route/view patterns
    "*_view",
    "*_endpoint",
    "*_route",
    "*_callback",
    "*_signal",
    "*_task",
)

# Top-level directories that are NOT packages — they're configuration,
# CI, docs, or platform metadata. The zombie-package detector splits paths
# on the first segment and treats everything as a candidate package; without
# this guard, dotfile dirs like `.github` get reported as "zombie packages
# with no importers" on every repo.
_NEVER_PACKAGE_DIRS: frozenset[str] = frozenset({
    ".github",
    ".gitlab",
    ".vscode",
    ".idea",
    ".aspire",
    ".config",
    ".devcenter",
    ".devcontainer",
    ".husky",
    ".changeset",
    ".azure",
    ".azuredevops",
    ".circleci",
    ".buildkite",
    ".cargo",
    ".husky",
    ".yarn",
    "docs",
    "doc",
    "documentation",
    "examples",
    "scripts",
    "assets",
    "static",
    "public",
    "tests",
    "test",
    "benches",
    "bench",
    "fuzz",
})


# Path segments that indicate test fixture / sample data directories.
_FIXTURE_PATH_SEGMENTS: tuple[str, ...] = (
    "fixture",
    "fixtures",
    "testdata",
    "test_data",
    "sample_repo",
    "mock_data",
    "test_assets",
)


# JSX namespace types discovered by the TypeScript compiler via tsconfig
# ``jsxImportSource`` / the global ``namespace JSX`` declaration — never
# imported by name from user code, but referenced implicitly by every
# JSX expression. A symbol with one of these names declared inside a
# ``namespace JSX`` block is an integration point with the JSX
# transformer, not dead code. The set is intentionally small and
# targeted; anything broader risks masking genuinely-unused exports.
_TS_JSX_NAMESPACE_TYPES: frozenset[str] = frozenset({
    "IntrinsicElements",
    "IntrinsicAttributes",
    "IntrinsicClassAttributes",
    "Element",
    "ElementType",
    "ElementClass",
    "ElementAttributesProperty",
    "ElementChildrenAttribute",
    "LibraryManagedAttributes",
})


def _is_fixture_path(path: str) -> bool:
    """Return True if path is under a test fixture / sample data directory."""
    path_lower = path.lower().replace("\\", "/")
    for seg in _FIXTURE_PATH_SEGMENTS:
        if f"/{seg}/" in path_lower or path_lower.startswith(f"{seg}/"):
            return True
    return False
