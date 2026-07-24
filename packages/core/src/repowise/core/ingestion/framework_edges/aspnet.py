"""ASP.NET / .NET framework edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


# A class becomes a controller when it is annotated [ApiController] OR its
# class name ends in "Controller" (the MVC discovery convention). The first
# is preferred — it produces zero false positives. `[...]`/`<...>` bracket
# class covers both C#'s `[ApiController]` and VB's `<ApiController>`.
_ASPNET_CONTROLLER_ATTR_RE = re.compile(r"[\[<]\s*ApiController\b")
_ASPNET_ROUTE_RE = re.compile(r"\[\s*(?:Http(?:Get|Post|Put|Delete|Patch|Options|Head)|Route)\b")
_ASPNET_MAP_CALL_RE = re.compile(
    r"\.\s*Map(?:Get|Post|Put|Delete|Patch|Controllers|Hub|GrpcService|Razor|Fallback)\s*[<(]"
)
_ASPNET_USE_MIDDLEWARE_RE = re.compile(r"\.\s*UseMiddleware\s*<\s*(\w+)")
# VB generic-argument syntax is `(Of T)` rather than `<T>` — kept as a
# separate pattern (not merged into one alternation) so each stays simple
# to read; call sites chain both iterators together.
_ASPNET_USE_MIDDLEWARE_RE_VB = re.compile(r"\.\s*UseMiddleware\s*\(\s*Of\s+(\w+)")
_DBCONTEXT_DECL_RE = re.compile(r"class\s+\w+\s*:\s*[\w.<>,\s]*\bDbContext\b")
# VB has no colon-delimited base list — `Inherits DbContext` is always its
# own statement, immediately after the `Class` header (see
# extractors/heritage/vbnet.py for the same shape).
_DBCONTEXT_DECL_RE_VB = re.compile(
    r"^[ \t]*Inherits\s+[\w.]*\bDbContext\b", re.MULTILINE | re.IGNORECASE
)
_DBSET_RE = re.compile(r"\bDbSet\s*<\s*([A-Z]\w*)\s*>")
_DBSET_RE_VB = re.compile(r"\bDbSet\s*\(\s*Of\s+([A-Z]\w*)\s*\)", re.IGNORECASE)

_ASPNET_LANGUAGES = ("csharp", "vbnet")


def _has_aspnet_imports(parsed_files: dict[str, Any]) -> bool:
    """True if any parsed file imports Microsoft.AspNetCore.* — cheap signal."""
    for parsed in parsed_files.values():
        if parsed.file_info.language not in _ASPNET_LANGUAGES:
            continue
        for imp in parsed.imports:
            if imp.module_path.startswith("Microsoft.AspNetCore"):
                return True
    return False


def _add_aspnet_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    """Add edges representing ASP.NET wiring.

    Three independent signal sources are merged:

    1. Application entry point (``Program.cs``/``Startup.cs``) → every
       controller file. ``MapControllers()`` is the discovery anchor;
       individual controllers are not statically referenced from
       ``Program.cs`` but the framework wires them at runtime.

    2. Application entry point → handler files for any ``app.MapGet(...)``
       / ``MapPost(...)`` minimal API call. We can't resolve the handler
       expression statically, so we approximate by linking to every file
       whose class is named in the handler argument (heuristic only).

    3. ``DbContext`` subclasses → files declaring the entity types named
       in their ``DbSet<T>`` properties. This surfaces EF Core's implicit
       persistence model wiring that no static import edge captures.

    Returns the number of edges added.
    """
    count = 0
    cs_files = [
        (path, parsed)
        for path, parsed in parsed_files.items()
        if parsed.file_info.language in _ASPNET_LANGUAGES and path in path_set
    ]
    if not cs_files:
        return 0

    # ---- 1. Discover controllers and entry points ----
    controllers: list[str] = []
    entry_points: list[str] = []
    dbcontext_files: list[tuple[str, str]] = []  # (path, source)
    type_decl_to_file: dict[str, str] = {}  # ClassName -> defining file path

    for path, parsed in cs_files:
        # Index every named class/struct/record this file declares so the
        # DbSet<T> step can resolve targets without re-parsing.
        for sym in parsed.symbols:
            if sym.kind in ("class", "struct", "record"):
                # Last-write wins is fine — duplicates are rare in well-formed code.
                type_decl_to_file[sym.name] = path

        text = read_text(parsed, encoding="utf-8-sig")
        if not text:
            continue
        if _ASPNET_CONTROLLER_ATTR_RE.search(text) or path.endswith(
            ("Controller.cs", "Controller.vb")
        ):
            controllers.append(path)
        name = Path(path).name
        if name in ("Program.cs", "Startup.cs", "Program.vb", "Startup.vb"):
            entry_points.append(path)
        if _DBCONTEXT_DECL_RE.search(text) or _DBCONTEXT_DECL_RE_VB.search(text):
            dbcontext_files.append((path, text))

    # ---- 2. Entry point → controllers (MapControllers / UseEndpoints) ----
    for entry in entry_points:
        for ctrl in controllers:
            if ctrl == entry:
                continue
            if _add_edge_if_new(graph, entry, ctrl):
                count += 1

    # ---- 3. Entry point → file containing handler class referenced in MapXxx ----
    # VB requires `AddressOf` before a method-group reference in this
    # position (`app.MapGet("/path", AddressOf Handler)`).
    handler_arg_re = re.compile(
        r"\.\s*Map(?:Get|Post|Put|Delete|Patch)\s*\(\s*[\"'][^\"']+[\"']\s*,\s*"
        r"(?:AddressOf\s+)?([A-Za-z_]\w*)",
        re.IGNORECASE,
    )
    for entry in entry_points:
        text = read_text(parsed_files[entry], encoding="utf-8-sig")
        if not text:
            continue
        for match in handler_arg_re.finditer(text):
            ident = match.group(1)
            target = type_decl_to_file.get(ident)
            if target and target in path_set and _add_edge_if_new(graph, entry, target):
                count += 1

    # ---- 4. UseMiddleware<T>() / UseMiddleware(Of T) ----
    for entry in entry_points:
        text = read_text(parsed_files[entry], encoding="utf-8-sig")
        if not text:
            continue
        for match in (
            *_ASPNET_USE_MIDDLEWARE_RE.finditer(text),
            *_ASPNET_USE_MIDDLEWARE_RE_VB.finditer(text),
        ):
            target = type_decl_to_file.get(match.group(1))
            if target and target in path_set and _add_edge_if_new(graph, entry, target):
                count += 1

    # ---- 5. DbContext → DbSet<T> / DbSet(Of T) entity files ----
    for db_path, db_text in dbcontext_files:
        for match in (*_DBSET_RE.finditer(db_text), *_DBSET_RE_VB.finditer(db_text)):
            entity = match.group(1)
            target = type_decl_to_file.get(entity)
            if target and target in path_set and _add_edge_if_new(graph, db_path, target):
                count += 1

    # NOTE: host-builder extension-method scanning (``app.MapCatalogApi()``)
    # moved to ``_add_dotnet_extension_edges`` so it fires on desktop .NET
    # (WPF/WinUI) too, not just ASP.NET — see audit item #28.

    return count


def _has_dotnet_files(parsed_files: dict[str, Any]) -> bool:
    return any(p.file_info.language in _ASPNET_LANGUAGES for p in parsed_files.values())


def _add_dotnet_extension_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    """Run the host-builder extension-method scan on any C#/VB.NET repo.

    Lifted out of ``_add_aspnet_edges`` so it also fires on desktop .NET
    (WPF / WinUI / WinForms) where ``Microsoft.AspNetCore.*`` is never
    imported but ``IServiceCollection`` / ``IModuleHost`` extension
    methods are still common. The host-type allowlist inside
    ``aspnet_extensions`` keeps false positives from generic ``Map(...)``
    LINQ calls at bay.
    """
    from ..aspnet_extensions import (
        add_extension_method_edges,
        collect_csharp_texts,
        collect_vbnet_texts,
    )

    dotnet_texts = {
        **collect_csharp_texts(parsed_files, path_set),
        **collect_vbnet_texts(parsed_files, path_set),
    }
    return add_extension_method_edges(graph, dotnet_texts, path_set)


class _AspNetHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        aspnet_in_stack = any(
            token in dctx.stack_lower
            for token in ("aspnet", "asp.net", "aspnetcore", "asp.net core")
        )
        return aspnet_in_stack or _has_aspnet_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_aspnet_edges(graph, parsed_files, path_set)


class _DotNetExtensionHandler:
    """Host-builder extension-method scan — runs on ANY C#/VB.NET repo (desktop .NET too)."""

    def detect(self, dctx: DetectionContext) -> bool:
        return _has_dotnet_files(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_dotnet_extension_edges(graph, parsed_files, path_set)


HANDLERS: list[FrameworkHandler] = [_AspNetHandler(), _DotNetExtensionHandler()]
