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
    "*/stdafx.h",
    "*/stdafx.cpp",
    "*ClassFactory.cpp",
    "*ClassFactory.h",
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


def _is_fixture_path(path: str) -> bool:
    """Return True if path is under a test fixture / sample data directory."""
    path_lower = path.lower().replace("\\", "/")
    for seg in _FIXTURE_PATH_SEGMENTS:
        if f"/{seg}/" in path_lower or path_lower.startswith(f"{seg}/"):
            return True
    return False
