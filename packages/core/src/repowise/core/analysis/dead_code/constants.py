"""Static configuration for dead-code detection.

These tuples / frozensets shape what the analyzer treats as "always
alive" (framework decorators, never-flag path globs) and where to skip
entirely (test fixture directories, non-code languages).

``never_flag_match`` lives here, beside its patterns, as a pure function of a
path, so every caller of :func:`~.file_reachability.is_file_reachable` shares
one matcher and one answer.
"""

from __future__ import annotations

import fnmatch
import os
import re
from functools import lru_cache

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY

# Non-code languages (registry passthrough languages plus "unknown").
_NON_CODE_LANGUAGES: frozenset[str] = _LANG_REGISTRY.unparseable_or_unknown_languages()

# Code languages whose files/symbols are entered through an external runtime
# that the static graph cannot observe (COBOL via JCL/schedulers, for example).
_DEAD_CODE_EXEMPT_LANGUAGES: frozenset[str] = (
    _NON_CODE_LANGUAGES | _LANG_REGISTRY.dead_code_exempt_languages()
)

# Patterns that should never be flagged as dead. ``fnmatch`` ``*`` spans ``/``,
# so a leading ``*`` matches nested and repo-root paths alike.
_NEVER_FLAG_PATTERNS: tuple[str, ...] = (
    # Shell scripts are invoked by name from CI, Makefiles and humans. Shell
    # parses to an AST, so it is not in ``_NON_CODE_LANGUAGES``; these globs
    # exempt it.
    "*.sh",
    "*.bash",
    "*.zsh",
    "*__init__.py",
    "*__main__.py",
    "*conftest.py",
    "*alembic/env.py",
    # Alembic loads version scripts reflectively; many setups use
    # ``alembic/versions/``, which ``*migrations*`` does not match.
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
    # ---- Qt / QML ---------------------------------------------------
    # Instantiated by type name and loaded by the runtime (qrc, Loader,
    # qmlRegisterType), so a QML file never has a static importer.
    "*.qml",
    # ---- Objective-C conventions ------------------------------------
    # App and scene delegates are named in Info.plist / the scene manifest
    # and instantiated by the runtime, never imported.
    "*AppDelegate.m",
    "*AppDelegate.h",
    "*SceneDelegate.m",
    "*SceneDelegate.h",
    # ---- .NET / C# conventions --------------------------------------
    # Implicit / generated / framework-loaded files that have no
    # static importers by design.
    "*GlobalUsings.cs",  # global usings — file-implicit, never imported by symbol
    "*.xaml.cs",  # XAML code-behind, wired by the source generator
    "*.xaml",
    "*.razor",
    "*.razor.cs",  # Blazor code-behind
    "*.razor.js",  # Blazor JS interop side-files
    "*.cshtml",
    "*.cshtml.cs",
    "*.designer.cs",  # Roslyn designer
    "*Designer.cs",
    "*.g.cs",  # Roslyn-generated
    "*.g.i.cs",
    "*.AssemblyInfo.cs",
    # VB.NET equivalents. "My Project" holds the generated Application,
    # Resources and Settings designers that no .vb file imports by name.
    "*.Designer.vb",
    "*.designer.vb",
    "*AssemblyInfo.vb",
    "*/My Project/*.vb",
    "*ApplicationEvents.vb",  # My.MyApplication hooks, raised by the VB runtime
    "*MauiProgram.cs",  # MAUI app entry — invoked by host, not imported
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
    # ASP.NET Core minimal-API modules, wired by ``app.MapXxxApi()`` in
    # ``Program.cs``, a call the import graph does not see.
    "*/Apis/*.cs",
    "*/Endpoints/*.cs",
    "*/Routes/*.cs",
    # ---- Generic .NET / Win32 conventions ----------------------------
    # Source-generator output directories, wired in at build time.
    "*/Generated/*.cs",
    "*/generated/*.cs",
    # Win32 P/Invoke surfaces, reached only via `[DllImport]` calls.
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
    # Test classes are loaded by the runner via attribute reflection. Both
    # locations and suffixes are matched, to catch tests at arbitrary paths.
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
    # Singular forms.
    "*UnitTest/*.cs",
    "*.UnitTest/*.cs",
    "*.Test/*.cs",
    "*/Wox.Test/*.cs",
    # ``UnitTests-<Subject>`` / ``UITest-<Subject>`` per-module test projects.
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
    # MSVC precompiled-header anchors are referenced by build settings; a COM
    # ``IClassFactory`` is activated by Windows via DllGetClassObject.
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
    # Apps, demos, examples, tools and benchmarks build to standalone
    # binaries (``add_executable`` / ``cc_binary``) with no importer.
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
    # C/C++ tool directories.
    "*/tools/*.cc",
    "*/tools/*.cpp",
    "*/tools/**/*.cc",
    "*/tools/**/*.cpp",
    "tools/*.cc",
    "tools/*.cpp",
    "tools/**/*.cc",
    "tools/**/*.cpp",
    # C/C++ test trees: test frameworks discover tests by glob, not import.
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
    # Any source under a ``tests/`` tree, for layouts without the
    # ``*_test.{cc,cpp}`` suffix convention.
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
    # Repo-rooted form of the ``*/tests/**`` block above.
    "tests/**/*.cc",
    "tests/**/*.cpp",
    "tests/**/*.cxx",
    "tests/**/*.h",
    "tests/**/*.hpp",
    # Test file suffixes outside a standard test directory.
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
    # Port / example skeleton headers: documentation, never built.
    "*/port_example.h",
    "*/port/port_example.h",
    "*_example.h",
    "*_example.hpp",
    "*_example.cc",
    # Build output roots, in case the walker lets them in.
    "*/build/**",
    "build/**",
    "*/cmake-build-*/**",
    "cmake-build-*/**",
    "*/_deps/**",
    "_deps/**",
    "*/out/build/**",
    "*/out/Debug/**",
    "*/out/Release/**",
    # Generated source files, wired in at build time.
    "moc_*.cpp",  # Qt MOC
    "moc_*.cc",
    "ui_*.h",  # Qt UIC
    "qrc_*.cpp",  # Qt RCC
    "qrc_*.cc",
    "*.moc",  # inline MOC includes
    "*.pb.cc",  # protoc generated
    "*.pb.h",
    "*.pb-c.c",  # protobuf-c
    "*.pb-c.h",
    "*.grpc.pb.cc",  # protoc-gen-grpc
    "*.grpc.pb.h",
    "*.capnp.c++",  # Cap'n Proto
    "*.capnp.h",
    "*.flatbuffers.h",
    "*_generated.h",  # FlatBuffers convention
    "*.tab.c",  # Bison / Yacc
    "*.tab.h",
    "*.yy.c",  # Flex / Lex
    "*_lex.cc",
    "*_wrap.cxx",  # SWIG
    "*_wrap.cpp",
    "*.cython.cpp",  # Cython
    # Vendored roots, C++ extensions (the ``.c`` / ``.h`` forms are below).
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
    # Integration tests (run via `cargo test`). ``**/`` needs a leading
    # directory, hence the bare repo-root forms.
    "**/tests/*.rs",
    "**/tests/**/*.rs",
    "tests/*.rs",
    "tests/**/*.rs",
    # Unit-test sibling modules (e.g. `src/foo/tests.rs`) are loaded by
    # `#[cfg(test)] mod tests;` and the Cargo test harness, not production imports.
    "**/src/**/tests.rs",
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
    # Run by ``go test``, never imported.
    "*_test.go",
    # ``package main`` entry points.
    "*/main.go",
    "main.go",
    # Package documentation stubs holding only the package doc comment.
    "*/doc.go",
    "doc.go",
    "*/docs.go",
    "docs.go",
    # Mage build files (``//go:build mage``, ``package main``) — run by the
    # ``mage`` tool, excluded from normal builds, never imported.
    "*/magefile.go",
    "magefile.go",
    # Generated code (stringer, protobuf, go-bindata, ``zz_generated*``).
    "*.gen.go",
    "*_gen.go",
    "*.pb.go",
    "*_string.go",
    "*zz_generated*.go",
    "*bindata.go",
    # ---- JavaScript conventions ------------------------------------------
    # Bundles and minified artifacts are served to the browser, not imported.
    "*.bundle.js",
    "*.min.js",
    "*.bundle.mjs",
    # Generated browser-global scripts loaded by a ``<script>`` tag.
    "*/livereload/gen/*",
    "livereload/gen/*",
    "*/livereload/livereload.js",
    "livereload/livereload.js",
    # Go's WASM JS glue, embedded and loaded by the host, not imported.
    "*wasm_exec.js",
    # ---- Vendored / third-party C ----------------------------------------
    # A vendored C library is used within its own translation units and
    # maintained upstream, so flagging its internals is noise.
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
    # Test files, discovered by the runner via filename glob.
    "*.test.ts",
    "*.test.tsx",
    "*.test.js",
    "*.test.jsx",
    "*.test.mjs",
    "*.test.cjs",
    "*.test.mts",
    "*.test.cts",
    "*.spec.ts",
    "*.spec.tsx",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.mjs",
    "*.spec.cjs",
    "*.spec.mts",
    "*.spec.cts",
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
    # More Next.js app router convention files. The ``*/foo.ts`` form requires
    # the literal basename, so ``mymiddleware.ts`` is not exempt.
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
    # Module / package metadata files, which declare no importable symbols.
    "*/module-info.java",
    "module-info.java",
    "*/package-info.java",
    "package-info.java",
    # Gradle / Maven test source sets. ``*Test`` / ``*Tests`` catch
    # project-specific source-set names without an allowlist.
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
    # Test file suffixes outside a standard source set.
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
    # Generated source roots (Gradle, Maven, KAPT, KSP, AOT).
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
    "*MapperImpl.java",  # MapStruct compile-time impl
    "*Dagger*.java",
    "*AutoValue_*.java",
    "*ServiceGrpc.java",
    "*OuterClass.java",  # protoc generated outer class
    "*$WrapperImpl.java",
    # ---- Dart / Flutter conventions ---------------------------------------
    # build_runner outputs, linked from their source via ``part``.
    "*.g.dart",
    "*.freezed.dart",  # freezed data classes
    "*.gr.dart",  # auto_route generated router
    "*.config.dart",  # injectable DI config
    "*.mocks.dart",  # mockito codegen
    "*.gen.dart",  # flutter_gen assets
    # package:test / integration_test runners execute these directly; no
    # static importer exists. ``example/`` is pub.dev's showcase convention.
    "*_test.dart",
    "*/integration_test/*",
    "*/test_driver/*",
    "*/example/*.dart",
    # Run directly (``dart run``), never imported.
    "bin/*.dart",
    "*/bin/*.dart",
    "*/tool/*.dart",
    "*/benchmark/*.dart",
    # build_runner wires lib/builder.dart in via build.yaml, not imports.
    "*/lib/builder.dart",
)

# Decorator patterns that indicate framework usage (route handlers, fixtures, etc.)
_FRAMEWORK_DECORATORS: tuple[str, ...] = (
    "pytest.fixture",
    "pytest.mark",
    # Unqualified ``from pytest import fixture`` form — the decorator text is
    # bare ``fixture``, which the dotted prefixes above never match.
    "fixture",
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
    # Bare-name match against ``@Foo``; wired in by reflection.
    "Component",
    "Service",
    "Repository",
    "Controller",
    "RestController",
    "Configuration",
    "ControllerAdvice",
    "RestControllerAdvice",
    "Mapper",  # MapStruct + MyBatis
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
    "Converter",  # JPA AttributeConverter
    "QuarkusMain",
    "QuarkusTest",
    "QuarkusIntegrationTest",
    "NativeImageTest",
    "MicronautApplication",
    "MicronautTest",
    "Path",  # JAX-RS
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
    "Schedule",  # Quarkus
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
    # OSGi lifecycle, called by the container via reflection.
    "Activate",
    "Deactivate",
    "Modified",
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
    "Container",  # Testcontainers
    "DynamicTest",
    "JsonCreator",  # Jackson factory method — reflectively invoked
    "JsonProperty",
    "Mojo",  # Maven plugin entry
    "Goal",
    "RegisterForReflection",
    # ---- JVM: routing / HTTP-verb annotations -----------------------
    # Route handlers, invoked by the framework dispatcher.
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
    "GET",  # JAX-RS / Quarkus / Micronaut
    "POST",
    "PUT",
    "DELETE",
    "PATCH",
    "HEAD",
    "OPTIONS",
    "Route",
)

# Decorator *suffixes* that indicate framework registration whatever the
# receiver is named: ``@my_group.command("add")`` registers with a locally named
# Click group, so the trailing attribute is the signal, not the prefix.
_FRAMEWORK_DECORATOR_SUFFIXES: tuple[str, ...] = (
    ".command",
    ".group",
    ".callback",
    # ``.register`` is deliberately absent: ``_is_framework_registered`` also
    # has to cover the bare ``@register_x`` form, so it owns both.
    # Route decorators on a locally named app or router.
    ".route",
    ".get",
    ".post",
    ".put",
    ".delete",
    ".patch",
    ".head",
    ".options",
    ".websocket",
    ".middleware",
    ".exception_handler",
    # Startup/shutdown hooks (``@app.on_event("shutdown")``).
    ".on_event",
    # Celery apps under a non-``app``/``celery`` local name (``@worker.task``).
    ".task",
    # Django template tags and filters, invoked by name from templates.
    ".tag",
    ".filter",
    ".simple_tag",
    ".inclusion_tag",
)

# Languages whose idiom is a static holder class the call site never names,
# because it names only the member (C# extension methods). A set so widening it
# is a deliberate act.
_CONTAINER_USE_LANGUAGES: frozenset[str] = frozenset({"csharp"})

# Annotations whose *argument* is the signal (``@SuppressWarnings("unused")``),
# matched against the raw decorator text rather than its base name.
_DELIBERATELY_UNUSED_ANNOTATIONS: tuple[tuple[str, str], ...] = (
    ("SuppressWarnings", "unused"),
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

# Top-level directories that are NOT packages (config, CI, docs, metadata). The
# zombie-package detector treats every first path segment as a candidate.
_NEVER_PACKAGE_DIRS: frozenset[str] = frozenset(
    {
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
    }
)


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


# JSX namespace types, referenced implicitly by every JSX expression rather
# than imported. Kept small: anything broader risks masking unused exports.
_TS_JSX_NAMESPACE_TYPES: frozenset[str] = frozenset(
    {
        "IntrinsicElements",
        "IntrinsicAttributes",
        "IntrinsicClassAttributes",
        "Element",
        "ElementType",
        "ElementClass",
        "ElementAttributesProperty",
        "ElementChildrenAttribute",
        "LibraryManagedAttributes",
    }
)


@lru_cache(maxsize=8)
def _never_flag_regex(patterns: tuple[str, ...]) -> re.Pattern[str]:
    """Compile *patterns* into one alternation regex equivalent to fnmatch.

    One pre-normcased alternation keeps ``fnmatch`` semantics at one regex
    match per node instead of one ``fnmatch`` call per (node, pattern).
    """
    return re.compile("|".join(fnmatch.translate(os.path.normcase(p)) for p in patterns))


@lru_cache(maxsize=8)
def _never_flag_suffix_index(
    patterns: tuple[str, ...],
) -> tuple[re.Pattern[str] | None, dict[str, re.Pattern[str]], tuple[int, ...]]:
    """Split *patterns* into suffix-keyed buckets that can be skipped wholesale.

    In one alternation every branch is tried and most begin ``.*``, so each
    scans the whole path.

    The filter is sound because ``fnmatch.translate`` ends *each* alternative
    with ``\\Z`` and the join keeps that per-branch (``(?s:A)\\Z|(?s:B)\\Z``),
    so every branch has to match the whole string. A pattern whose text after
    its last ``*`` is the literal ``S`` can therefore only match a path ending
    in ``S``: testing it against a path that does not end in ``S`` is wasted
    work, never a dropped match. Patterns ending in ``*`` constrain nothing at
    the tail and stay in one always-tried group.

    Branches keep their translated form, so ``fnmatch``'s atomic groups
    behave as in the single alternation; order is irrelevant to a boolean.

    Returns ``(always_tried_regex_or_None, {suffix: regex}, suffix_lengths)``.
    """
    always: list[str] = []
    by_suffix: dict[str, list[str]] = {}
    for pattern in patterns:
        norm = os.path.normcase(pattern)
        # No pattern in the set uses ``?`` or a character class (checked by
        # test_never_flag_suffix_index_covers_pattern_shapes), so the text
        # after the last ``*`` is a plain literal tail.
        suffix = norm.rsplit("*", 1)[-1]
        translated = fnmatch.translate(norm)
        if suffix:
            by_suffix.setdefault(suffix, []).append(translated)
        else:
            always.append(translated)
    compiled = {s: re.compile("|".join(v)) for s, v in by_suffix.items()}
    return (
        re.compile("|".join(always)) if always else None,
        compiled,
        tuple(sorted({len(s) for s in compiled})),
    )


@lru_cache(maxsize=131072)
def never_flag_match(path: str) -> bool:
    """Memoized never-flag match for the default pattern set.

    Equivalent to ``_never_flag_regex(_NEVER_FLAG_PATTERNS).match(...)``, and
    pinned to it path-for-path by ``test_never_flag_regex.py``. The pattern
    set is a module constant, so process-wide memoization is sound; several
    detector passes ask about the same node ids.
    """
    norm = os.path.normcase(path)
    always, by_suffix, suffix_lengths = _never_flag_suffix_index(_NEVER_FLAG_PATTERNS)
    if always is not None and always.match(norm):
        return True
    for length in suffix_lengths:
        if length > len(norm):
            break
        bucket = by_suffix.get(norm[-length:])
        if bucket is not None and bucket.match(norm):
            return True
    return False


def _is_fixture_path(path: str) -> bool:
    """Return True if path is under a test fixture / sample data directory."""
    path_lower = path.lower().replace("\\", "/")
    for seg in _FIXTURE_PATH_SEGMENTS:
        if f"/{seg}/" in path_lower or path_lower.startswith(f"{seg}/"):
            return True
    return False
