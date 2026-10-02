"""C# files the compiler or a build tool consumes without a source reference.

Four kinds of file have no caller in source and are used all the same:

* a polyfill the compiler looks up by name (``IsExternalInit`` is what an
  ``init`` accessor or a ``record`` compiles against on an older framework);
* a class a tool finds by the interface it implements (EF Core's
  ``dotnet ef`` loads the ``IDesignTimeDbContextFactory<T>`` in the project);
* a marker a source generator reads from the compilation (Vogen's
  ``[EfCoreConverter<T>]`` class is the generator's input);
* a file-based app, started by ``dotnet run <file>``: it opens with ``#:``
  directives (``#:sdk``, ``#:package``), as Polly's Cake script ``cake.cs`` does.

The first three mark a file a reachability root when every top-level type in
it is one of these. A file that also declares an ordinary type is left to the
usual checks, so a dead sibling still reports.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .base import DetectionContext, FrameworkHandler, read_text

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers import ResolverContext

_LANGUAGE = "csharp"
_TYPE_KINDS = frozenset({"class", "interface", "struct", "enum", "record"})

#: Types the compiler binds to by fully qualified name, so a copy declared in
#: the project is used by every ``init`` accessor, ``required`` member and
#: nullable annotation it compiles.
_COMPILER_CONSUMED_TYPES: dict[str, frozenset[str]] = {
    _LANGUAGE: frozenset(
        {
            "System.Runtime.CompilerServices.IsExternalInit",
            "System.Runtime.CompilerServices.RequiredMemberAttribute",
            "System.Runtime.CompilerServices.CompilerFeatureRequiredAttribute",
            "System.Runtime.CompilerServices.NullableAttribute",
            "System.Runtime.CompilerServices.NullableContextAttribute",
        }
    )
}

#: Interfaces a tool discovers implementations of by reflection. EF Core's
#: design-time tooling (``dotnet ef``, the package manager console) instantiates
#: them; the application never does.
_TOOL_DISCOVERED_INTERFACES: dict[str, frozenset[str]] = {
    _LANGUAGE: frozenset({"IDesignTimeDbContextFactory", "IDesignTimeServices"})
}

#: Attributes that make a class the input of a source generator. Vogen reads
#: ``[EfCoreConverter<T>]`` off a ``partial class`` and emits the converters.
_GENERATOR_MARKER_ATTRIBUTES: dict[str, frozenset[str]] = {
    _LANGUAGE: frozenset({"EfCoreConverter"})
}

# A ``partial class Name;`` has no body, so the parser reports no symbol for
# it and the marker has to be read from the text.
_MARKER_ATTRIBUTE_RE = re.compile(r"\[\s*(?:[\w.]+\.)?(\w+)\s*<")

# The opening of a file-based app: an optional BOM, shebang and ``//`` lines,
# then a ``#:sdk`` / ``#:package`` / ``#:property`` directive.
_APP_DIRECTIVE_RE = re.compile(
    r"\A(?:﻿)?(?:#![^\n]*\n)?\s*(?://[^\n]*\n\s*)*#:[a-z]+[ \t]", re.IGNORECASE
)
_DIRECTIVE_HEAD_CHARS = 512


def _namespaces(symbols: list[Any]) -> dict[str, Any]:
    return {s.name: s for s in symbols if s.kind == "module"}


def _namespace_of(sym: Any, modules: dict[str, Any]) -> str:
    """The namespace *sym* is declared in, nested blocks joined with dots."""
    parts: list[str] = []
    name = sym.parent_name
    while name in modules and name not in parts:
        parts.append(name)
        name = modules[name].parent_name
    if not parts:
        # File-scoped ``namespace A.B;``: the one module with no parent.
        roots = [m.name for m in modules.values() if m.parent_name is None]
        return roots[0] if len(roots) == 1 else ""
    return ".".join(reversed(parts))


def _short_name(dotted: str) -> str:
    """``A.B.Name<T>`` -> ``Name``."""
    return dotted.split("<", 1)[0].rsplit(".", 1)[-1].strip()


def _is_consumed(sym: Any, parsed: Any, modules: dict[str, Any]) -> bool:
    namespace = _namespace_of(sym, modules)
    if f"{namespace}.{sym.name}" in _COMPILER_CONSUMED_TYPES[_LANGUAGE]:
        return True
    discovered = _TOOL_DISCOVERED_INTERFACES[_LANGUAGE]
    if any(
        rel.child_name == sym.name
        and rel.kind == "implements"
        and _short_name(rel.parent_name) in discovered
        for rel in parsed.heritage
    ):
        return True
    markers = _GENERATOR_MARKER_ATTRIBUTES[_LANGUAGE]
    return any(_short_name(d) in markers for d in sym.decorators)


def _is_generator_input(parsed: Any) -> bool:
    """A typeless file carrying a generator marker on a ``;``-bodied class."""
    text = read_text(parsed, encoding="utf-8-sig")
    markers = _GENERATOR_MARKER_ATTRIBUTES[_LANGUAGE]
    return any(m.group(1) in markers for m in _MARKER_ATTRIBUTE_RE.finditer(text))


def _is_file_based_app(parsed: Any) -> bool:
    """Whether the file opens with ``#:`` directives, so ``dotnet run`` starts it."""
    try:
        with open(parsed.file_info.abs_path, encoding="utf-8", errors="ignore") as handle:
            head = handle.read(_DIRECTIVE_HEAD_CHARS)
    except OSError:
        return False
    return _APP_DIRECTIVE_RE.match(head) is not None


def _is_consumed_file(parsed: Any) -> bool:
    if _is_file_based_app(parsed):
        return True
    modules = _namespaces(parsed.symbols)
    types = [
        s
        for s in parsed.symbols
        if s.kind in _TYPE_KINDS and (not s.parent_name or s.parent_name in modules)
    ]
    if not types:
        return _is_generator_input(parsed)
    return all(_is_consumed(s, parsed, modules) for s in types)


def consumed_files(parsed_files: dict[str, Any]) -> list[str]:
    """C# files the compiler or a tool uses with no reference in source."""
    return [
        path
        for path, parsed in parsed_files.items()
        if parsed.file_info.language == _LANGUAGE and _is_consumed_file(parsed)
    ]


def _mark_roots(graph: nx.DiGraph, parsed_files: dict[str, Any], path_set: set[str]) -> None:
    for path in consumed_files(parsed_files):
        node = graph.nodes.get(path)
        if path in path_set and node is not None:
            node["is_reachability_root"] = True


class _DotNetRootsHandler:
    """Marks files the compiler or a tool uses; adds no edges."""

    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.file_info.language == _LANGUAGE for p in dctx.parsed_files.values())

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        _mark_roots(graph, parsed_files, path_set)
        return 0


HANDLERS: list[FrameworkHandler] = [_DotNetRootsHandler()]
