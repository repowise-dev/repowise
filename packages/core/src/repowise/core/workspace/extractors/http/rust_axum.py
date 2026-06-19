"""Rust HTTP provider dialect.

Covers the route-declaration shapes used by the common Rust web frameworks:

* **Axum** — ``.route("/path", get(handler))``, including method routers that
  chain several verbs (``get(h).post(h2)``);
* **Actix-web / Rocket** — attribute-macro routes (``#[get("/path")]``).

Warp's filter-combinator routing (``warp::path!(...)``) has no stable literal
path to anchor on and is intentionally not modelled here.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import RUST
from .dialect import build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Axum: the head of a `.route("/path", <method-router>)` call. The method router
# itself is parsed separately so chained verbs (`get(h).post(h2)`) are all found.
_AXUM_ROUTE_HEAD_RE = re.compile(r"""\.route\s*\(\s*["']([^"']+)["']\s*,""")

# Method-router verbs inside a `.route(...)` second argument, or the verbs used
# by `MethodRouter`/`on` builders. Lower-case function form, e.g. `get(`, `post(`.
_AXUM_METHOD_RE = re.compile(r"""\b(get|post|put|delete|patch|head|options|trace)\s*\(""")

# Actix-web / Rocket attribute macros: #[get("/path")], #[post("/path", ...)].
_RUST_ATTR_ROUTE_RE = re.compile(
    r"""#\[\s*(get|post|put|delete|patch|head|options)\s*\(\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


def _line_window(content: str, start: int) -> str:
    """Return the text from *start* up to the next newline.

    Axum routes are written one per line, so the method router for a given
    ``.route(...)`` lives between the path literal and the end of the line.
    """
    nl = content.find("\n", start)
    return content[start:] if nl == -1 else content[start:nl]


class RustAxumDialect:
    name = "rust-axum"
    extensions = RUST

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        out: list[Contract] = []

        # Axum `.route("/path", get(...).post(...))`.
        for m in _AXUM_ROUTE_HEAD_RE.finditer(content):
            path_raw = m.group(1)
            window = _line_window(content, m.end())
            methods = {mm.group(1).upper() for mm in _AXUM_METHOD_RE.finditer(window)}
            for method in sorted(methods):
                c = build_provider_contract(
                    ctx, method=method, path_raw=path_raw, framework="axum"
                )
                if c is not None:
                    out.append(c)

        # Actix-web / Rocket attribute macros.
        for m in _RUST_ATTR_ROUTE_RE.finditer(content):
            c = build_provider_contract(
                ctx, method=m.group(1).upper(), path_raw=m.group(2), framework="rust-attr"
            )
            if c is not None:
                out.append(c)

        return out
