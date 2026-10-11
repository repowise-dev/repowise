"""JAX-RS HTTP provider dialect — Jakarta EE, Quarkus, Jersey, RESTEasy.

``@GET`` names the verb, the method's own ``@Path`` names the sub-path, and the
nearest type-level ``@Path`` above it is the prefix — the same two-level shape
the Spring dialect stitches, with the annotations split apart.

The annotations are recognised by ``ingestion.framework_routes``, shared with
the graph-edge builder that reads them to stamp a ``jax_rs_resource`` role.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    jaxrs_class_paths,
    jaxrs_routes,
)

from ..base import line_at
from ..langs import JAVA, KOTLIN
from .dialect import build_provider_contract, nearest_prefix
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


class JaxRsDialect:
    name = "jaxrs"
    extensions = JAVA | KOTLIN

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if "@Path" not in content:
            return []
        class_paths = jaxrs_class_paths(content)
        out: list[Contract] = []
        for route in jaxrs_routes(content):
            if route.verb not in HTTP_METHODS:
                continue
            path = compose_prefix(nearest_prefix(class_paths, route.offset), route.path or "")
            c = build_provider_contract(
                ctx,
                method=route.verb,
                path_raw=path,
                framework="jaxrs",
                line=line_at(content, route.offset),
            )
            if c is not None:
                out.append(c)
        return out
