"""Axum / Actix / Rocket (Rust) router convention edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move. The
axum ``.route(...)`` recogniser lives in ``ingestion.framework_routes``, shared
with the contract extractor that reads the same call for its path. Actix's
builder (``web::get().to(h)``) is a different construct from the attribute macro
the contract side reads, so only axum overlaps.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Any

from ..framework_routes import axum_routes
from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_function_to_file,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


_RUST_ACTIX_TO_RE = re.compile(
    r"web::\s*(?:get|post|put|delete|patch|head)\(\)\s*\.\s*to\s*\(\s*([\w:]+)\s*\)"
)
_RUST_ACTIX_SERVICE_RE = re.compile(r"\.\s*service\s*\(\s*([\w:]+)\s*\)")
_RUST_SCOPE_CONFIGURE_RE = re.compile(r"\.\s*configure\s*\(\s*([\w:]+)\s*\)")
_RUST_NEST_RE = re.compile(r"\.\s*nest\s*\(\s*[\"'][^\"']*[\"']\s*,\s*([\w:]+)\s*\)")
_RUST_LAYER_RE = re.compile(
    r"\.\s*layer\s*\(\s*(?:axum::middleware::from_fn\s*\(\s*)?([\w:]+)\s*\)"
)
_RUST_FALLBACK_RE = re.compile(r"\.\s*fallback\s*\(\s*([\w:]+)\s*\)")
_RUST_WITH_STATE_RE = re.compile(r"\.\s*with_state\s*\(\s*([\w:]+)\s*\)")
_RUST_ROCKET_MOUNT_RE = re.compile(
    r"\.\s*mount\s*\(\s*[\"'][^\"']*[\"']\s*,\s*routes!\s*\[([^\]]+)\]"
)


def _has_rust_router_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language != "rust":
            continue
        for imp in parsed.imports:
            mp = imp.module_path
            if mp.startswith(("axum", "actix_web", "actix-web", "rocket")):
                return True
    return False


def _add_rust_router_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    count = 0
    func_to_files = _build_function_to_file(parsed_files, ("rust",))

    def _resolve(handler: str) -> list[str]:
        name = handler.rsplit("::", 1)[-1]
        return list(func_to_files.get(name, []))

    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "rust":
            continue
        text = read_text(parsed)
        if not text:
            continue
        for route in axum_routes(text):
            if not route.handler:
                continue  # a closure or an `on(MethodFilter::GET, ...)` filter
            for target in _resolve(route.handler):
                if target != path and target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

        for regex in (
            _RUST_ACTIX_TO_RE,
            _RUST_ACTIX_SERVICE_RE,
            _RUST_SCOPE_CONFIGURE_RE,
            _RUST_NEST_RE,
            _RUST_LAYER_RE,
            _RUST_FALLBACK_RE,
            _RUST_WITH_STATE_RE,
        ):
            for m in regex.finditer(text):
                for target in _resolve(m.group(1)):
                    if (
                        target != path
                        and target in path_set
                        and _add_edge_if_new(graph, path, target)
                    ):
                        count += 1

        # Rocket routes![] macro lists multiple handlers comma-separated
        for m in _RUST_ROCKET_MOUNT_RE.finditer(text):
            for handler in m.group(1).split(","):
                handler = handler.strip()
                if handler:
                    for target in _resolve(handler):
                        if (
                            target != path
                            and target in path_set
                            and _add_edge_if_new(graph, path, target)
                        ):
                            count += 1

    return count


_RUST_INCLUDE_RE = re.compile(
    r'\b(?:core::|std::)?include(?:_str|_bytes)?!\s*[\(\[\{]\s*(?:r(#*)"(.*?)"\1|"((?:[^"\\]|\\.)*)")\s*[\)\]\}]'
)


def _resolve_include_target(
    base: str,
    spec: str | None,
    path: str,
    all_paths: set[str],
) -> str | None:
    if not spec or spec.startswith("/") or "\n" in spec:
        return None
    normalized = posixpath.normpath(posixpath.join(base, spec.replace("\\\\", "/").replace("\\", "/")))
    if not normalized.startswith("..") and normalized != path and normalized in all_paths:
        return normalized
    return None


def _add_rust_include_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    all_paths = (ctx.path_set if ctx and ctx.path_set else set()) | path_set
    count = 0
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "rust":
            continue
        text = read_text(parsed)
        if not text or "include" not in text:
            continue
        base = posixpath.dirname(path)
        for m in _RUST_INCLUDE_RE.finditer(text):
            spec = m.group(2) if m.group(2) is not None else m.group(3)
            target = _resolve_include_target(base, spec, path, all_paths)
            if target and _add_edge_if_new(graph, path, target):
                count += 1
    return count


class _RustIncludeHandler:
    """``include_str!``, ``include_bytes!`` and ``include!`` embed a file by path."""

    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.endswith(".rs") for p in dctx.path_set)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_rust_include_edges(graph, parsed_files, ctx, path_set)


class _RustRouterHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return _has_rust_router_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_rust_router_edges(graph, parsed_files, path_set)


HANDLERS: list[FrameworkHandler] = [_RustRouterHandler(), _RustIncludeHandler()]
