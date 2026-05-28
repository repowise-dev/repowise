"""Source-text markers for runtime / dynamic dispatch.

When a repo uses ``importlib.import_module``, ``import()``,
``Class.forName()``, etc., unreachable modules in the same package may
be loaded at runtime. The dead-code analyzer scans for these markers to
lower confidence on findings within their packages.

Phase 2 work (A1/A2 in ``docs/LANGUAGE_REMAINING_WORK.md``) will:

- expand the marker dicts to cover Go, Ruby, PHP, Kotlin, Swift, Scala,
- and / or replace this text-scan with consumption of ``edge_type="dynamic"``
  edges produced by the ``dynamic_hints`` extractors.

Keep new entries grouped by file extension so the per-language audit
in Phase 2 stays mechanical.
"""

from __future__ import annotations

from pathlib import Path


# Patterns in source that indicate dynamic/runtime imports, keyed by suffix.
_DYNAMIC_IMPORT_MARKERS: dict[str, tuple[str, ...]] = {
    ".py": (
        "importlib.import_module",
        "__import__(",
        "importlib.reload",
        "pkgutil.iter_modules",
    ),
    # JS/TS dynamic-dispatch markers. Each implies the surrounding file
    # (and, by the package-co-location heuristic, its siblings) reaches
    # code through a runtime mechanism the static import graph cannot
    # observe — runtime ``import()``, vitest/jest test mocking, React
    # lazy loading, Webpack ``require.context``, Vite ``import.meta.glob``,
    # Next.js server/client component boundaries.
    ".js": (
        "import(",
        "require(",
        "require.resolve(",
        "require.context(",
        "import.meta.glob(",
        "import.meta.globEager(",
        "import.meta.url",
        "import.meta.resolve(",
        "React.lazy(",
        "lazy(() =>",
        "next/dynamic",
        "jest.mock(",
        "vi.mock(",
        "vi.doMock(",
        "'use server'",
        "'use client'",
        '"use server"',
        '"use client"',
    ),
    ".mjs": (
        "import(",
        "require(",
        "import.meta.glob(",
        "import.meta.url",
        "import.meta.resolve(",
        "vi.mock(",
    ),
    ".cjs": ("require(", "require.resolve(", "require.context(", "jest.mock("),
    ".ts": (
        "import(",
        "require(",
        "require.context(",
        "import.meta.glob(",
        "import.meta.globEager(",
        "import.meta.url",
        "import.meta.resolve(",
        "React.lazy(",
        "lazy(() =>",
        "next/dynamic",
        "jest.mock(",
        "vi.mock(",
        "vi.doMock(",
        "'use server'",
        "'use client'",
        '"use server"',
        '"use client"',
    ),
    ".tsx": (
        "import(",
        "require(",
        "import.meta.glob(",
        "import.meta.globEager(",
        "import.meta.url",
        "React.lazy(",
        "lazy(() =>",
        "next/dynamic",
        "jest.mock(",
        "vi.mock(",
        "vi.doMock(",
        "'use server'",
        "'use client'",
        '"use server"',
        '"use client"',
    ),
    ".java": (
        # Core reflection
        "Class.forName(",
        "ServiceLoader.load(",
        ".loadClass(",
        ".getContextClassLoader(",
        "Method.invoke(",
        ".invoke(",
        "Constructor.newInstance(",
        ".newInstance(",
        ".getMethod(",
        ".getDeclaredMethod(",
        ".getField(",
        ".getDeclaredField(",
        "Proxy.newProxyInstance(",
        # Test / mock / JSON / mapping factories that resolve concrete
        # types at runtime by their .class literal
        "Mockito.mock(",
        "Mockito.spy(",
        "Mappers.getMapper(",
        ".readValue(",
        ".fromJson(",
        # Spring Boot bootstrap + container lookups
        "SpringApplication.run(",
        ".getBean(",
        # DI annotations whose mere presence implies framework wiring
        "@Autowired",
        "@Inject",
        "@Resource",
        "@Bean",
        "@ConfigurationProperties",
        "@Value(",
        "@RegisterForReflection",
        "@JsonCreator",
        "@JsonSubTypes",
        # SnakeYAML / Jackson polymorphic loading
        "TypeReference<",
    ),
    ".kt": (
        "Class.forName(",
        "ServiceLoader.load(",
        "KClass.createInstance(",
        "::class.java",
        "::class.objectInstance",
        ".loadClass(",
        "Method.invoke(",
        ".invoke(",
        ".newInstance(",
        "Mockito.mock(",
        "Mockito.spy(",
        "mockk<",
        "mockk(",
        "Mappers.getMapper(",
        ".readValue(",
        ".fromJson(",
        "runApplication<",          # Spring Boot Kotlin bootstrap
        "SpringApplication.run(",
        ".getBean(",
        "@Autowired",
        "@Inject",
        "@Resource",
        "@Bean",
        "@ConfigurationProperties",
        "@RegisterForReflection",
        "@JsonCreator",
    ),
    ".rb": (
        "autoload ",
        "const_get(",
        "send(:require",
        "Object.send(",
        "Kernel.const_get(",
        ".public_send(",
    ),
    ".php": (
        "class_exists(",
        "interface_exists(",
        "call_user_func(",
        "call_user_func_array(",
        "new $",
        "ReflectionClass(",
    ),
    ".go": (
        "plugin.Open(",
        "reflect.New(",
        "reflect.TypeOf(",
        "reflect.ValueOf(",
        # Compiler / linker directives. Files that drive code generation
        # (``//go:generate``), embed assets (``//go:embed``), or link to a
        # symbol defined elsewhere (``//go:linkname``) participate in the
        # build outside the import graph — treat their package as dynamic.
        # (Struct tags like ``json:"…"`` were considered but rejected: they
        # appear in nearly every Go file, so they are too broad to be a
        # useful dynamic signal.)
        "//go:generate",
        "//go:embed",
        "//go:linkname",
    ),
    ".swift": (
        "NSClassFromString(",
        "Selector(",
        "#selector(",
        "NSStringFromClass(",
    ),
    ".scala": (
        "Class.forName(",
        "runtimeMirror(",
        "reflect.runtime",
    ),
    ".rs": (
        # Trait object construction (runtime dispatch)
        "Box<dyn ",
        "Arc<dyn ",
        "Rc<dyn ",
        "&dyn ",
        # FFI exports (called from C, no Rust callers)
        '#[no_mangle]',
        'extern "C"',
        # Plugin/inventory registration (resolved at link time)
        "inventory::submit!",
        "linkme::distributed_slice!",
        # Dynamic dispatch through function pointers
        "Box::new(",
        "Arc::new(",
        # Serde (generates field access code)
        '#[derive(Serialize',
        '#[derive(Deserialize',
        '#[serde(',
        # Conditional compilation
        '#[cfg(target_',
        '#[cfg(feature',
        # Proc-macro registration (called by the compiler, not by user code)
        '#[proc_macro]',
        '#[proc_macro_derive',
        '#[proc_macro_attribute',
        # Doc-hidden items are intentionally not part of the public API surface
        # but may still be used by downstream crates or macros via re-exports.
        '#[doc(hidden)]',
        # Explicitly suppressed warnings — the author knows it looks unused.
        '#[allow(dead_code)]',
        '#[allow(unused)]',
        # Deprecated items are intentionally present but winding down.
        '#[deprecated',
    ),
    # C — function-pointer / dynamic-loading idioms shared with C++.
    ".c": (
        "dlopen(",
        "dlsym(",
        "dlmopen(",
        "LoadLibrary(",
        "LoadLibraryA(",
        "LoadLibraryW(",
        "LoadLibraryEx",
        "GetProcAddress(",
        "__attribute__((constructor))",
        "__attribute__((destructor))",
        "__attribute__((used))",
        "__attribute__((weak))",
        "[[gnu::retain]]",
        "[[gnu::used]]",
        "LLVMFuzzerTestOneInput",
        "JNI_OnLoad",
    ),
    # C++ — all of the C markers plus framework / registration / signal
    # / slot idioms invoking symbols by macro expansion or runtime
    # registration. Each token's presence in the file means at least one
    # symbol is reached through a path the static graph cannot observe.
    ".cc": (
        "dlopen(",
        "dlsym(",
        "dlmopen(",
        "LoadLibrary(",
        "LoadLibraryA(",
        "LoadLibraryW(",
        "GetProcAddress(",
        "Q_OBJECT",
        "Q_GADGET",
        "Q_NAMESPACE",
        "QObject::connect(",
        "QML_ELEMENT",
        "SIGNAL(",
        "SLOT(",
        "TEST(",
        "TEST_F(",
        "TEST_P(",
        "TYPED_TEST(",
        "INSTANTIATE_TEST_SUITE_P(",
        "FRIEND_TEST(",
        "BOOST_AUTO_TEST_CASE",
        "BOOST_FIXTURE_TEST_CASE",
        "BOOST_AUTO_TEST_SUITE",
        "CATCH_TEST_CASE",
        "TEST_CASE(",
        "SCENARIO(",
        "DOCTEST_TEST_CASE",
        "BENCHMARK(",
        "BENCHMARK_F(",
        "BENCHMARK_REGISTER_F(",
        "BENCHMARK_MAIN(",
        "LLVMFuzzerTestOneInput",
        "LLVMFuzzerInitialize",
        "REGISTER_OP(",
        "REGISTER_KERNEL_BUILDER(",
        "BOOST_CLASS_EXPORT",
        "PLUGINLIB_EXPORT_CLASS(",
        "RCLCPP_COMPONENTS_REGISTER_NODE(",
        "PYBIND11_MODULE(",
        "PYBIND11_EMBEDDED_MODULE(",
        "BOOST_PYTHON_MODULE(",
        "NAPI_MODULE(",
        "EM_JS(",
        "EM_ASM",
        "__attribute__((constructor))",
        "__attribute__((destructor))",
        "__attribute__((used))",
        "__attribute__((weak))",
        "[[gnu::retain]]",
        "[[gnu::used]]",
        "JNI_OnLoad",
        'extern "C"',
    ),
    ".cpp": (
        "dlopen(",
        "dlsym(",
        "LoadLibrary(",
        "GetProcAddress(",
        "Q_OBJECT",
        "Q_GADGET",
        "Q_NAMESPACE",
        "QObject::connect(",
        "QML_ELEMENT",
        "SIGNAL(",
        "SLOT(",
        "TEST(",
        "TEST_F(",
        "TEST_P(",
        "TYPED_TEST(",
        "INSTANTIATE_TEST_SUITE_P(",
        "BOOST_AUTO_TEST_CASE",
        "BOOST_FIXTURE_TEST_CASE",
        "CATCH_TEST_CASE",
        "TEST_CASE(",
        "SCENARIO(",
        "DOCTEST_TEST_CASE",
        "BENCHMARK(",
        "BENCHMARK_F(",
        "BENCHMARK_MAIN(",
        "LLVMFuzzerTestOneInput",
        "REGISTER_OP(",
        "REGISTER_KERNEL_BUILDER(",
        "BOOST_CLASS_EXPORT",
        "PLUGINLIB_EXPORT_CLASS(",
        "RCLCPP_COMPONENTS_REGISTER_NODE(",
        "PYBIND11_MODULE(",
        "PYBIND11_EMBEDDED_MODULE(",
        "BOOST_PYTHON_MODULE(",
        "NAPI_MODULE(",
        "__attribute__((constructor))",
        "__attribute__((used))",
        "[[gnu::retain]]",
        "[[gnu::used]]",
        "JNI_OnLoad",
        'extern "C"',
    ),
    ".cxx": (
        "Q_OBJECT",
        "Q_GADGET",
        "QObject::connect(",
        "SIGNAL(",
        "SLOT(",
        "TEST(",
        "TEST_F(",
        "BOOST_AUTO_TEST_CASE",
        "TEST_CASE(",
        "BENCHMARK(",
        "PYBIND11_MODULE(",
        "PYBIND11_EMBEDDED_MODULE(",
        "__attribute__((constructor))",
        "__attribute__((used))",
        "[[gnu::retain]]",
        'extern "C"',
    ),
    # C++ headers — Q_OBJECT macro expands to a vtable + MOC entry, so a
    # header with ``Q_OBJECT`` participates in the Qt meta-object
    # protocol the static graph cannot trace.
    ".h": (
        "Q_OBJECT",
        "Q_GADGET",
        "Q_NAMESPACE",
        "Q_DECLARE_METATYPE(",
        "Q_PROPERTY(",
        "QML_ELEMENT",
        "BOOST_CLASS_EXPORT",
        "PYBIND11_MODULE(",
        "PYBIND11_EMBEDDED_MODULE(",
        "__attribute__((constructor))",
        "__attribute__((used))",
        "[[gnu::retain]]",
        'extern "C"',
    ),
    ".hpp": (
        "Q_OBJECT",
        "Q_GADGET",
        "Q_NAMESPACE",
        "Q_DECLARE_METATYPE(",
        "Q_PROPERTY(",
        "QML_ELEMENT",
        "BOOST_CLASS_EXPORT",
        "PYBIND11_MODULE(",
        "PYBIND11_EMBEDDED_MODULE(",
        "__attribute__((constructor))",
        "__attribute__((used))",
        "[[gnu::retain]]",
        'extern "C"',
    ),
    ".hxx": (
        "Q_OBJECT",
        "Q_GADGET",
        "BOOST_CLASS_EXPORT",
        "__attribute__((constructor))",
        "__attribute__((used))",
        'extern "C"',
    ),
    ".cs": (
        # Reflection-driven type loading
        "Type.GetType(",
        "Activator.CreateInstance(",
        "Assembly.Load(",
        "Assembly.LoadFrom(",
        "Assembly.LoadFile(",
        "GetExecutingAssembly().GetTypes(",
        # Cross-assembly visibility — types named in the friend assembly
        # may be used externally even with no static call site.
        "[assembly: InternalsVisibleTo",
        # Trim-safe reflection annotation
        "[DynamicDependency",
        # MEF / VS extensibility composition
        "[Export",
        "[ImportMany",
        # DI registration: types registered here have no static caller
        # but the framework instantiates them at runtime. Three forms.
        "AddScoped<",
        "AddSingleton<",
        "AddTransient<",
        "AddHostedService<",
    ),
}


def find_dynamic_import_files(parsed_files: dict) -> set[str]:
    """Return the set of file paths whose source contains a dynamic-import marker."""
    result: set[str] = set()
    for path, pf in parsed_files.items():
        try:
            file_info = getattr(pf, "file_info", None)
            if file_info is None:
                continue
            src_path = Path(file_info.abs_path)
            markers = _DYNAMIC_IMPORT_MARKERS.get(src_path.suffix)
            if not markers:
                continue
            source = src_path.read_text(errors="ignore")
            if any(marker in source for marker in markers):
                result.add(path)
        except Exception:
            continue
    return result


def find_dynamic_edge_files(graph) -> set[str]:
    """Return the set of file paths involved in dynamic graph edges.

    An edge counts as dynamic when its ``edge_type`` is ``"dynamic"`` or
    starts with ``"dynamic_"`` (semantic sub-types like ``"dynamic_uses"``,
    ``"dynamic_imports"``, ``"url_route"`` after the graph-builder prefix).
    Both endpoints contribute: the source's file and the target's file
    (or the node id itself when nodes are file paths).
    """
    if graph is None:
        return set()
    result: set[str] = set()
    try:
        for u, v, data in graph.edges(data=True):
            etype = data.get("edge_type", "")
            if etype != "dynamic" and not etype.startswith("dynamic_"):
                continue
            for endpoint in (u, v):
                if endpoint is None:
                    continue
                endpoint_str = str(endpoint)
                if endpoint_str.startswith("external:"):
                    continue
                node_data = graph.nodes.get(endpoint, {})
                file_path = node_data.get("file_path")
                if file_path:
                    result.add(str(file_path))
                else:
                    result.add(endpoint_str)
    except Exception:
        return result
    return result
