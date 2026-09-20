"""Flask HTTP provider dialect: ``@app.route('/users/<int:id>')``.

The decorator is recognised by ``ingestion.framework_routes``, shared with the
graph-edge builder that reads the same ``register_blueprint`` call.

The real path is three segments stitched together, the same two-level shape
FastAPI has: a cross-file ``register_blueprint(bp, url_prefix=...)`` mount, the
in-file ``Blueprint(..., url_prefix=...)`` binding, and the decorator path.
Flask lets the registration override the blueprint's own prefix, so the mount
wins wherever both are present.

Ceilings, both stated rather than guessed at:

- ``add_url_rule('/x', view_func=...)`` registers a route without a decorator.
  It is not read, so a repo that registers that way publishes fewer endpoints
  than it serves.
- ``MethodView`` classes, which reach the route table through ``as_view()`` on
  that same call, follow from it and are equally out.
- A ``methods=`` list or a ``url_prefix=`` naming a constant rather than a
  literal refuses the route: the verbs, or the mount, are spelled in another
  file, and serving the decorator's own path would publish an endpoint nothing
  answers.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    flask_blueprints,
    flask_routes,
)

from ..base import line_at
from ..langs import PYTHON
from .dialect import build_provider_contract
from .mounts import compose_prefix, router_prefixes

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Namespaced so a blueprint name cannot collide with a FastAPI router variable
# in the one mount map every provider dialect shares.
_MOUNT_PREFIX = "flask:"

# `route`, `get` and `post` are ordinary attribute names, so the file has to
# prove its decorators are Flask's before they become endpoints: without this
# an `@app.route` on any other object is read as a route.
_FLASK_IMPORT_RE = re.compile(
    r"^[^\S\n]*(?:from\s+flask(?:\.[\w.]+)?\s+import\b|import\s+flask\b)|flask\.Blueprint",
    re.MULTILINE,
)

#: Names conventionally holding an app or a blueprint where the binding is in
#: another file (an application factory, a package ``__init__``).
_DEFAULT_APP_NAMES = frozenset({"app", "bp", "blueprint", "api"})

#: Recorded for a blueprint registered at a prefix this cannot read
#: (``url_prefix=PREFIX``). The blueprint is mounted somewhere, and not at the
#: prefix it declares itself, so its routes are refused rather than published at
#: a path nothing serves.
_UNKNOWN_MOUNT = "?unresolved"


def flask_file(content: str) -> bool:
    """True when *content* is a Flask module, so its decorators are routes.

    Every other reader of a Python file asks this before treating ``@x.route``
    or ``@x.get`` as an endpoint: both are ordinary attribute names, and the
    import is the only thing in the text that says otherwise.
    """
    return "flask" in content and _FLASK_IMPORT_RE.search(content) is not None


class FlaskDialect:
    name = "flask"
    extensions = PYTHON

    def collect_mounts(self, content: str) -> dict[str, str]:
        """``register_blueprint(bp, url_prefix=...)`` mounts declared in *content*.

        Keyed by the blueprint expression's final name segment (``views.bp`` ->
        ``bp``), which is the name its own file binds. Only registrations
        carrying an explicit prefix are recorded; the rest leave the blueprint's
        own ``url_prefix`` in charge. A prefix the registration names but this
        cannot read is recorded as unknown, which refuses the routes below it.
        """
        if not flask_file(content):
            return {}
        out: dict[str, str] = {}
        for mount in flask_blueprints(content):
            if mount.prefix is None:
                out[_MOUNT_PREFIX + mount.var.split(".")[-1]] = _UNKNOWN_MOUNT
            elif mount.prefix:
                out[_MOUNT_PREFIX + mount.var.split(".")[-1]] = mount.prefix
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        if not flask_file(ctx.content):
            return []
        prefixes = router_prefixes(ctx.content, "Blueprint|Flask")
        known = set(prefixes) | _DEFAULT_APP_NAMES

        out: list[Contract] = []
        for route in flask_routes(ctx.content):
            if route.receiver not in known or route.verb not in HTTP_METHODS:
                continue
            # A registration prefix replaces the blueprint's own, as Flask does.
            mount = ctx.mounts.get(_MOUNT_PREFIX + route.receiver)
            if mount == _UNKNOWN_MOUNT:
                continue
            prefix = prefixes.get(route.receiver, "") if mount is None else mount
            c = build_provider_contract(
                ctx,
                method=route.verb,
                path_raw=compose_prefix(prefix, route.path or ""),
                framework="flask",
                line=line_at(ctx.content, route.offset),
                handler=route.handler,
            )
            if c is not None:
                out.append(c)
        return out
