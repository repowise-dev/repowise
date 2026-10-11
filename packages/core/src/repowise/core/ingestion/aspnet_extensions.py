"""ASP.NET host-builder extension-method resolution.

ASP.NET Core minimal-API and DI registration code lives in static
classes exposing extension methods that take ``this
IEndpointRouteBuilder`` / ``this IServiceCollection`` /
``this IApplicationBuilder`` (and similar) as their first parameter.
The call site looks like ``app.MapCatalogApi();`` or
``services.AddCatalogServices()`` and binds to a definition like:

.. code-block:: csharp

    public static IEndpointRouteBuilder MapCatalogApi(
        this IEndpointRouteBuilder app)
    { ... }

The static graph never connects ``Program.cs`` (the caller) to the
file that defines the extension method, so the defining file shows up
as an orphan and the surrounding endpoint module reads as dead code.

This module closes the gap with a two-pass scan that lives next to
:mod:`framework_edges` but is kept separate for clarity and unit
testability:

1. **Definition pass** — find every ``public static T MapXxx(this
   <host> ...)`` or ``AddXxx(this <host> ...)`` signature and index
   it by method name.
2. **Call-site pass** — find every ``.MapXxx(`` / ``.AddXxx(`` call
   in the same set of C# files and emit a framework edge from the
   caller file to the defining file.

The host-type allowlist is conservative (well-known ASP.NET builder
types) to avoid binding e.g. ``list.Map(...)`` LINQ to an unrelated
extension method.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import networkx as nx


# Host types whose extension methods we want to link. Restricting the
# allowlist avoids false positives from generic ``T``-parameterised
# helpers and from LINQ-style ``Map`` on collections.
_ASPNET_HOST_TYPES: tuple[str, ...] = (
    "IEndpointRouteBuilder",
    "IApplicationBuilder",
    "IServiceCollection",
    "WebApplication",
    "WebApplicationBuilder",
    "IHostBuilder",
    "IHostApplicationBuilder",
    "IMvcBuilder",
    "IRouteBuilder",
)


# ``public static <return-type> <name>(this <host-type> ...``
# - return-type is permissive (any token sequence up to the name) so we
#   handle ``IEndpointRouteBuilder``, ``RouteHandlerBuilder``, ``void``,
#   ``IServiceCollection`` etc.
# - method name must start with ``Map`` or ``Add`` (the only two
#   prefixes ASP.NET conventionally uses for host extensions).
_EXTENSION_DEF_RE = re.compile(
    r"\bpublic\s+static\s+[\w<>,\s\.\?\[\]]+?\s+"
    r"(?P<name>(?:Map|Add|Use)[A-Z]\w*)\s*"
    r"(?:<[^>]+>\s*)?"  # optional generic param list
    r"\(\s*this\s+"
    r"(?P<host>[\w\.]+)"
    r"\b"
)


_EXTENSION_CALL_RE = re.compile(r"\.\s*(?P<name>(?:Map|Add|Use)[A-Z]\w*)\s*\(")


def build_extension_index(cs_texts: dict[str, str]) -> dict[str, str]:
    """Map ``Map*`` / ``Add*`` / ``Use*`` extension-method name → defining file.

    *cs_texts* maps repo-relative C# file path to its source text.
    Last-write-wins on duplicate method names — duplicates are rare in
    well-formed solutions and last-wins is no worse than any other
    arbitrary tie-break for graph attachment.
    """
    index: dict[str, str] = {}
    host_allow = set(_ASPNET_HOST_TYPES)
    for path, text in cs_texts.items():
        for m in _EXTENSION_DEF_RE.finditer(text):
            host = m.group("host")
            # Tolerate ``Microsoft.Extensions.DependencyInjection.IServiceCollection``
            host_short = host.rsplit(".", 1)[-1]
            if host_short not in host_allow:
                continue
            index[m.group("name")] = path
    return index


def add_extension_method_edges(
    graph: nx.DiGraph,
    cs_texts: dict[str, str],
    path_set: set[str],
) -> int:
    """Emit framework edges from caller files to extension-method definitions.

    Returns the number of edges added. Edges are tagged
    ``edge_type="framework"`` so they feed dead-code's existing
    "incoming framework edge ⇒ live" check.
    """
    if not cs_texts:
        return 0

    index = build_extension_index(cs_texts)
    if not index:
        return 0

    count = 0
    for caller_path, text in cs_texts.items():
        if caller_path not in path_set:
            continue
        seen_for_caller: set[str] = set()
        for m in _EXTENSION_CALL_RE.finditer(text):
            name = m.group("name")
            target = index.get(name)
            if target is None or target == caller_path:
                continue
            if target in seen_for_caller or target not in path_set:
                continue
            if _add_edge_if_new(graph, caller_path, target):
                seen_for_caller.add(target)
                count += 1
    return count


def _add_edge_if_new(graph: nx.DiGraph, source: str, target: str) -> bool:
    """Add a framework edge if no edge already connects source → target."""
    if source == target or graph.has_edge(source, target):
        return False
    graph.add_edge(source, target, edge_type="framework", imported_names=[])
    return True


def collect_csharp_texts(parsed_files: dict[str, Any], path_set: set[str]) -> dict[str, str]:
    """Read each C# file's source text once, keyed by repo-relative path.

    Returning a dict keeps the two passes (definition + call site)
    cache-friendly: large solutions otherwise read every .cs file
    twice from disk just for this analysis.
    """
    texts: dict[str, str] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "csharp":
            continue
        if path not in path_set:
            continue
        try:
            texts[path] = Path(parsed.file_info.abs_path).read_text(
                encoding="utf-8-sig", errors="ignore"
            )
        except OSError:
            continue
    return texts
