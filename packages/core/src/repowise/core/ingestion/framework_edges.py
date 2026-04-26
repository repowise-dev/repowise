"""Framework-aware synthetic edge detection.

Extracted from ``graph.py`` — detects Django, FastAPI, Flask, and pytest
convention-based relationships and adds ``edge_type="framework"`` edges.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .resolvers import ResolverContext, resolve_import

if TYPE_CHECKING:
    import networkx as nx

    from .models import ParsedFile


def add_framework_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, ParsedFile],
    ctx: ResolverContext,
    tech_stack: list[str] | None = None,
) -> int:
    """Add synthetic edges for framework-mediated relationships.

    Returns the number of edges added.
    """
    count = 0
    path_set = set(parsed_files.keys())

    # Always run: pytest conftest detection
    count += _add_conftest_edges(graph, path_set)

    stack_lower = {s.lower() for s in (tech_stack or [])}

    if "django" in stack_lower:
        count += _add_django_edges(graph, path_set)
    if "fastapi" in stack_lower or "starlette" in stack_lower:
        count += _add_fastapi_edges(graph, parsed_files, ctx, path_set)
    if "flask" in stack_lower:
        count += _add_flask_edges(graph, parsed_files, ctx, path_set)

    # ASP.NET framework edges run when the tech stack hints at .NET web,
    # OR when any .cs file imports Microsoft.AspNetCore.* (cheap fallback
    # so we don't depend on detect_tech_stack catching the project).
    aspnet_in_stack = any(
        token in stack_lower
        for token in ("aspnet", "asp.net", "aspnetcore", "asp.net core")
    )
    if aspnet_in_stack or _has_aspnet_imports(parsed_files):
        count += _add_aspnet_edges(graph, parsed_files, path_set)

    return count


def _add_edge_if_new(graph: nx.DiGraph, source: str, target: str) -> bool:
    """Add a framework edge if no edge already exists. Returns True if added."""
    if source == target:
        return False
    if graph.has_edge(source, target):
        return False
    graph.add_edge(source, target, edge_type="framework", imported_names=[])
    return True


def _add_conftest_edges(graph: nx.DiGraph, path_set: set[str]) -> int:
    """conftest.py -> test files in the same or child directories."""
    count = 0
    conftest_paths = [p for p in path_set if Path(p).name == "conftest.py"]

    for conf in conftest_paths:
        conf_dir = Path(conf).parent.as_posix()
        prefix = f"{conf_dir}/" if conf_dir != "." else ""
        for p in path_set:
            if p == conf:
                continue
            node = graph.nodes.get(p, {})
            if not node.get("is_test", False):
                continue
            if (p.startswith(prefix) or (prefix == "" and "/" not in p)) and _add_edge_if_new(
                graph, p, conf
            ):
                count += 1
    return count


def _add_django_edges(graph: nx.DiGraph, path_set: set[str]) -> int:
    """Django conventions: admin->models, urls->views in the same directory."""
    count = 0
    by_dir: dict[str, dict[str, str]] = {}
    for p in path_set:
        pp = Path(p)
        d = pp.parent.as_posix()
        by_dir.setdefault(d, {})[pp.stem] = p

    for _d, stems in by_dir.items():
        if (
            "admin" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["admin"], stems["models"])
        ):
            count += 1
        if (
            "urls" in stems
            and "views" in stems
            and _add_edge_if_new(graph, stems["urls"], stems["views"])
        ):
            count += 1
        if (
            "forms" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["forms"], stems["models"])
        ):
            count += 1
        if (
            "serializers" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["serializers"], stems["models"])
        ):
            count += 1
    return count


def _add_fastapi_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    """Detect include_router() calls and link app files to router modules."""
    count = 0
    var_to_file: dict[str, str] = {}

    for path, parsed in parsed_files.items():
        for imp in parsed.imports:
            for name in imp.imported_names:
                if name.lower().endswith("router") or name.lower().endswith("app"):
                    resolved = resolve_import(
                        imp.module_path,
                        path,
                        parsed.file_info.language,
                        ctx,
                    )
                    if resolved and resolved in path_set:
                        var_to_file[name] = resolved

    router_re = re.compile(r"(?:include_router|add_api_route)\s*\(\s*(\w+)")
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "python":
            continue
        try:
            source = Path(parsed.file_info.abs_path).read_text(errors="ignore")
        except Exception:
            continue
        for match in router_re.finditer(source):
            var_name = match.group(1)
            target = var_to_file.get(var_name)
            if target and target in path_set and _add_edge_if_new(graph, path, target):
                count += 1
    return count


def _add_flask_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    """Detect register_blueprint() calls and link app files to blueprint modules."""
    count = 0
    var_to_file: dict[str, str] = {}

    for path, parsed in parsed_files.items():
        for imp in parsed.imports:
            for name in imp.imported_names:
                if "blueprint" in name.lower() or name.lower().endswith("bp"):
                    resolved = resolve_import(
                        imp.module_path,
                        path,
                        parsed.file_info.language,
                        ctx,
                    )
                    if resolved and resolved in path_set:
                        var_to_file[name] = resolved

    bp_re = re.compile(r"register_blueprint\s*\(\s*(\w+)")
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "python":
            continue
        try:
            source = Path(parsed.file_info.abs_path).read_text(errors="ignore")
        except Exception:
            continue
        for match in bp_re.finditer(source):
            var_name = match.group(1)
            target = var_to_file.get(var_name)
            if target and target in path_set and _add_edge_if_new(graph, path, target):
                count += 1
    return count


# ---------------------------------------------------------------------------
# ASP.NET / .NET framework edges
# ---------------------------------------------------------------------------

# A class becomes a controller when it is annotated [ApiController] OR its
# class name ends in "Controller" (the MVC discovery convention). The first
# is preferred — it produces zero false positives.
_ASPNET_CONTROLLER_ATTR_RE = re.compile(r"\[\s*ApiController\b")
_ASPNET_ROUTE_RE = re.compile(r"\[\s*(?:Http(?:Get|Post|Put|Delete|Patch|Options|Head)|Route)\b")
_ASPNET_MAP_CALL_RE = re.compile(
    r"\.\s*Map(?:Get|Post|Put|Delete|Patch|Controllers|Hub|GrpcService|Razor|Fallback)\s*[<(]"
)
_ASPNET_USE_MIDDLEWARE_RE = re.compile(r"\.\s*UseMiddleware\s*<\s*(\w+)")
_DBCONTEXT_DECL_RE = re.compile(r"class\s+\w+\s*:\s*[\w.<>,\s]*\bDbContext\b")
_DBSET_RE = re.compile(r"\bDbSet\s*<\s*([A-Z]\w*)\s*>")


def _has_aspnet_imports(parsed_files: dict[str, Any]) -> bool:
    """True if any parsed file imports Microsoft.AspNetCore.* — cheap signal."""
    for parsed in parsed_files.values():
        if parsed.file_info.language != "csharp":
            continue
        for imp in parsed.imports:
            if imp.module_path.startswith("Microsoft.AspNetCore"):
                return True
    return False


def _read_cs_text(parsed: Any) -> str:
    try:
        return Path(parsed.file_info.abs_path).read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return ""


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
        if parsed.file_info.language == "csharp" and path in path_set
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

        text = _read_cs_text(parsed)
        if not text:
            continue
        if _ASPNET_CONTROLLER_ATTR_RE.search(text) or path.endswith("Controller.cs"):
            controllers.append(path)
        name = Path(path).name
        if name in ("Program.cs", "Startup.cs"):
            entry_points.append(path)
        if _DBCONTEXT_DECL_RE.search(text):
            dbcontext_files.append((path, text))

    # ---- 2. Entry point → controllers (MapControllers / UseEndpoints) ----
    for entry in entry_points:
        for ctrl in controllers:
            if ctrl == entry:
                continue
            if _add_edge_if_new(graph, entry, ctrl):
                count += 1

    # ---- 3. Entry point → file containing handler class referenced in MapXxx ----
    handler_arg_re = re.compile(
        r"\.\s*Map(?:Get|Post|Put|Delete|Patch)\s*\(\s*[\"'][^\"']+[\"']\s*,\s*([A-Za-z_]\w*)"
    )
    for entry in entry_points:
        text = _read_cs_text(parsed_files[entry])
        if not text:
            continue
        for match in handler_arg_re.finditer(text):
            ident = match.group(1)
            target = type_decl_to_file.get(ident)
            if target and target in path_set and _add_edge_if_new(graph, entry, target):
                count += 1

    # ---- 4. UseMiddleware<T>() ----
    middleware_re = _ASPNET_USE_MIDDLEWARE_RE
    for entry in entry_points:
        text = _read_cs_text(parsed_files[entry])
        if not text:
            continue
        for match in middleware_re.finditer(text):
            target = type_decl_to_file.get(match.group(1))
            if target and target in path_set and _add_edge_if_new(graph, entry, target):
                count += 1

    # ---- 5. DbContext → DbSet<T> entity files ----
    for db_path, db_text in dbcontext_files:
        for match in _DBSET_RE.finditer(db_text):
            entity = match.group(1)
            target = type_decl_to_file.get(entity)
            if target and target in path_set and _add_edge_if_new(graph, db_path, target):
                count += 1

    return count
