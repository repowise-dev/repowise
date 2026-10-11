"""Rust HTTP provider dialect.

Covers the route-declaration shapes used by the common Rust web frameworks:

* **Axum** — ``.route("/path", get(handler))``, including method routers that
  chain several verbs (``get(h).post(h2)``). Recognised by
  ``ingestion.framework_routes``, shared with the graph-edge builder that reads
  the same call for its handler argument;
* **Actix-web / Rocket** — attribute-macro routes (``#[get("/path")]``). Actix's
  builder form (``web::get().to(h)``) is a different construct that only the
  graph side reads, so it is not shared.

Warp's filter-combinator routing (``warp::path!(...)``) has no stable literal
path to anchor on and is intentionally not modelled here.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import axum_routes

from ..base import line_at
from ..langs import RUST
from .dialect import build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Actix-web / Rocket attribute macros: #[get("/path")], #[post("/path", ...)].
_RUST_ATTR_ROUTE_RE = re.compile(
    r"""#\[\s*(get|post|put|delete|patch|head|options)\s*\(\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


class RustAxumDialect:
    name = "rust-axum"
    extensions = RUST

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        out: list[Contract] = []

        # One call site yields a match per verb, so collapse repeats of a verb.
        seen: set[tuple[int, str]] = set()
        for route in axum_routes(content):
            # `on(MethodFilter::GET, h)` names no literal verb.
            if route.verb == "ON" or (route.offset, route.verb) in seen:
                continue
            seen.add((route.offset, route.verb))
            c = build_provider_contract(
                ctx,
                method=route.verb,
                path_raw=route.path or "",
                framework="axum",
                line=line_at(content, route.offset),
                handler=route.handler,
            )
            if c is not None:
                out.append(c)

        # Actix-web / Rocket attribute macros.
        for m in _RUST_ATTR_ROUTE_RE.finditer(content):
            c = build_provider_contract(
                ctx,
                method=m.group(1).upper(),
                path_raw=m.group(2),
                framework="rust-attr",
                line=line_at(content, m.start()),
            )
            if c is not None:
                out.append(c)

        return out
