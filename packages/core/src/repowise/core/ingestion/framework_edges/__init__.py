"""Framework-aware synthetic edge detection.

Detects convention-based relationships (Django, FastAPI, Flask, ASP.NET, Rails,
Laravel, Spring, Express/Nest, Gin/Echo/Chi, Axum/Actix/Rocket, TYPO3, and
pytest ``conftest.py``) and adds ``edge_type="framework"`` edges that no static
import graph captures.

Previously a single ``framework_edges.py`` module; split (PR 3.5) into one
module per framework behind this façade. The public entry point —
``add_framework_edges`` — is unchanged; it now iterates an ordered list of
:class:`~.base.FrameworkHandler` objects, preserving the original detection
order (and therefore which framework "wins" a shared edge via
:func:`~.base._add_edge_if_new`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import (
    android_manifest,
    aspnet,
    django,
    express,
    fastapi,
    flask,
    flutter,
    go,
    gtest,
    hono,
    jakarta,
    laravel,
    micronaut,
    next_app,
    pytest_edges,
    quarkus,
    rails,
    remix,
    rust,
    spring,
    trpc,
    typo3,
)
from .base import DetectionContext, FrameworkHandler, _add_edge_if_new, read_text

if TYPE_CHECKING:
    import networkx as nx

    from ..models import ParsedFile
    from ..resolvers import ResolverContext

# Ordered exactly as the original ``add_framework_edges`` invoked them — the
# order is load-bearing because ``_add_edge_if_new`` is first-wins.
_HANDLERS: list[FrameworkHandler] = [
    *pytest_edges.HANDLERS,  # always runs
    *django.HANDLERS,
    *fastapi.HANDLERS,
    *flask.HANDLERS,
    *aspnet.HANDLERS,  # ASP.NET edges, then the any-C# extension-method scan
    *rails.HANDLERS,
    *laravel.HANDLERS,
    *spring.HANDLERS,
    *jakarta.HANDLERS,
    *quarkus.HANDLERS,
    *micronaut.HANDLERS,
    *android_manifest.HANDLERS,
    *flutter.HANDLERS,
    *express.HANDLERS,
    *next_app.HANDLERS,
    *hono.HANDLERS,
    *remix.HANDLERS,
    *trpc.HANDLERS,
    *go.HANDLERS,
    *gtest.HANDLERS,
    *rust.HANDLERS,
    *typo3.HANDLERS,
]


def add_framework_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, ParsedFile],
    ctx: ResolverContext,
    tech_stack: list[str] | None = None,
) -> int:
    """Add synthetic edges for framework-mediated relationships.

    Returns the number of edges added.
    """
    path_set = set(parsed_files.keys())
    stack_lower = {s.lower() for s in (tech_stack or [])}
    dctx = DetectionContext(
        stack_lower=stack_lower,
        parsed_files=parsed_files,
        ctx=ctx,
        path_set=path_set,
    )

    count = 0
    for handler in _HANDLERS:
        if handler.detect(dctx):
            count += handler.add_edges(graph, parsed_files, ctx, path_set)
    return count


__all__ = [
    "DetectionContext",
    "FrameworkHandler",
    "_add_edge_if_new",
    "add_framework_edges",
    "read_text",
]
