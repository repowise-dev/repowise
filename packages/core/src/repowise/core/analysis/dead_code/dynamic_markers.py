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
    ".js": ("import(", "require(", "require.resolve("),
    ".mjs": ("import(", "require("),
    ".cjs": ("require(", "require.resolve("),
    ".ts": ("import(", "require("),
    ".tsx": ("import(", "require("),
    ".java": ("Class.forName(", "ServiceLoader.load("),
    ".kt": (
        "Class.forName(",
        "ServiceLoader.load(",
        "KClass.createInstance(",
        "::class.java",
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
