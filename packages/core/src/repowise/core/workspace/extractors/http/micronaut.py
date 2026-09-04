"""Micronaut HTTP provider dialect.

``@Get`` names the verb and its argument the sub-path; the nearest
``@Controller`` above it is the prefix. That is the two-level shape Spring and
JAX-RS also serve, written with the verb in the annotation's own name and no
``Mapping`` suffix, so the import is what tells the three apart: ``@Get`` and
``@Controller`` are spelled the same in Spring's annotation set, on files this
dialect reads too.

The annotations are recognised by ``ingestion.framework_routes``, shared with
the graph-edge builder that reads ``@Controller`` to stamp a component role.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    micronaut_annotations,
    micronaut_class_paths,
    micronaut_client_types,
    micronaut_routes,
)

from ..base import line_at
from ..langs import JAVA, KOTLIN
from .dialect import build_provider_contract
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

#: The package every routing annotation this dialect reads comes from.
_MICRONAUT_ANNOTATIONS = "io.micronaut.http.annotation"


def _nearest(offsets: list[int], pos: int) -> int:
    """The last offset declared before *pos*, or -1."""
    return max((o for o in offsets if o < pos), default=-1)


def _nearest_controller(
    class_paths: list[tuple[int, str | None]], pos: int
) -> tuple[int, str | None] | None:
    """The last ``(offset, prefix)`` controller declared before *pos*, or None.

    The prefix travels with the offset because a controller whose prefix could
    not be read is still the type a route sits under: dropping it would hand
    the route to whatever was declared above.
    """
    found: tuple[int, str | None] | None = None
    for offset, prefix in class_paths:
        if offset >= pos:
            break
        found = (offset, prefix)
    return found


class MicronautDialect:
    name = "micronaut"
    extensions = JAVA | KOTLIN

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if _MICRONAUT_ANNOTATIONS not in content:
            return []
        # One scan of the file's annotations, read by all three recognisers.
        annotations = micronaut_annotations(content)
        class_paths = micronaut_class_paths(content, annotations)
        clients = micronaut_client_types(content, annotations)
        out: list[Contract] = []
        for route in micronaut_routes(content, annotations):
            # HEAD and OPTIONS are Micronaut verbs the contract layer does not
            # record; a path it could not read is refused rather than served at
            # the class prefix.
            if route.verb not in HTTP_METHODS or route.path is None:
                continue
            # A verb annotation under a `@Client` type is a call this repo
            # makes, and one under no type-level annotation at all is the
            # interface such a client implements. Only a `@Controller` serves.
            controller = _nearest_controller(class_paths, route.offset)
            if controller is None or controller[0] <= _nearest(clients, route.offset):
                continue
            # A prefix named by a constant is not this file's to read, and the
            # sub-path on its own is not where the route is served.
            if controller[1] is None:
                continue
            path = compose_prefix(controller[1], route.path)
            c = build_provider_contract(
                ctx,
                method=route.verb,
                path_raw=path,
                framework="micronaut",
                line=line_at(content, route.offset),
                handler=route.handler,
            )
            if c is not None:
                out.append(c)
        return out
