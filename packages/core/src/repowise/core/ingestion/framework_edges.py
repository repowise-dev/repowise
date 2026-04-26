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
