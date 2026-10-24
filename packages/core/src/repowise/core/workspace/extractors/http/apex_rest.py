"""Salesforce Apex REST provider dialect.

``@RestResource(urlMapping='/accounts/*')`` with ``@HttpGet`` serves
``GET /services/apexrest/accounts/{param}``: ``*`` is read as one path parameter.
Recognition lives in ``ingestion.framework_routes``, shared with the graph edges.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import HTTP_METHODS, apex_rest_routes

from ..base import line_at
from ..langs import APEX
from .dialect import build_provider_contract
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

#: Where an org serves every ``@RestResource`` class.
APEX_REST_BASE = "/services/apexrest"


def apex_rest_path(url_mapping: str) -> str:
    """The request path a ``urlMapping`` serves, ``*`` read as a path parameter."""
    return compose_prefix(APEX_REST_BASE, url_mapping.replace("*", "{param}"))


class ApexRestDialect:
    name = "apex-rest"
    extensions = APEX
    # Apex is case-insensitive; `apex_rest_routes` reads the exact spelling.
    requires = ("RestResource", "restresource", "Restresource", "RESTRESOURCE", "restResource")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        out: list[Contract] = []
        for route in apex_rest_routes(content):
            # A mapping held in a constant is a path, just not this file's to know.
            if route.verb not in HTTP_METHODS or route.path is None:
                continue
            c = build_provider_contract(
                ctx,
                method=route.verb,
                path_raw=apex_rest_path(route.path),
                framework="apex-rest",
                line=line_at(content, route.offset),
            )
            if c is not None:
                out.append(c)
        return out
