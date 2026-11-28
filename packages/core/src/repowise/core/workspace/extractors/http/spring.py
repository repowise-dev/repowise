"""Spring (Java / Kotlin) HTTP provider dialect.

``@GetMapping("/users")`` names the verb and sub-path; the ``@RequestMapping``
on the type around it is the prefix. The annotations are recognised by
``ingestion.framework_routes``, shared with the graph-edge builder that links
each route to its handler method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    micronaut_annotations,
    spring_class_paths,
    spring_route_prefixes,
    spring_routes,
)

from ..base import line_at
from ..calls import outside_comments
from ..langs import JAVA, KOTLIN
from .dialect import build_provider_contract
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


class SpringDialect:
    name = "spring"
    extensions = JAVA | KOTLIN
    requires = ("Mapping",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        annotations = micronaut_annotations(content)
        class_paths = spring_class_paths(content, annotations)
        out: list[Contract] = []
        for route in outside_comments(ctx, spring_routes(content, annotations)):
            # An unrestricted `@RequestMapping` serves every verb, which the
            # contract layer has no method for; an unreadable path is refused.
            if route.verb not in HTTP_METHODS or route.path is None:
                continue
            for prefix in spring_route_prefixes(class_paths, route.offset) or ():
                c = build_provider_contract(
                    ctx,
                    method=route.verb,
                    # An empty mapping on an unmapped class is the root route.
                    path_raw=compose_prefix(prefix, route.path) or "/",
                    framework="spring",
                    line=line_at(content, route.offset),
                )
                if c is not None:
                    out.append(c)
        return out
