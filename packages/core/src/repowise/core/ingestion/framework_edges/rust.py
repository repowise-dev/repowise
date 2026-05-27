"""Axum / Actix / Rocket (Rust) router convention edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

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


_RUST_AXUM_ROUTE_RE = re.compile(
    r"\.\s*route\s*\(\s*[\"'][^\"']*[\"']\s*,\s*"
    r"(?:get|post|put|delete|patch|head|options|on)\s*\(\s*([\w:]+)\s*\)"
)
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
        for regex in (
            _RUST_AXUM_ROUTE_RE,
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


class _RustRouterHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        rust_router_in_stack = any(
            token in dctx.stack_lower for token in ("axum", "actix", "actix-web")
        )
        return rust_router_in_stack or _has_rust_router_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_rust_router_edges(graph, parsed_files, path_set)


HANDLERS: list[FrameworkHandler] = [_RustRouterHandler()]
