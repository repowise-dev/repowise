"""Django HTTP provider dialect — ``urls.py`` URLconf entries.

The entry is recognised by ``ingestion.framework_routes``, shared with the
graph-edge builder that reads the same entry for its view expression.

A URLconf names no method, so every route is recorded as ``*`` (as the Go
dialect does for ``HandleFunc``). Prefixes come from ``include(...)`` in another
URLconf, resolved through the orchestrator's repo-wide mount map.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import django_includes, django_routes

from ..base import line_at
from ..langs import PYTHON
from .dialect import build_provider_contract
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Namespaced so a dotted module cannot collide with another dialect's router
# variable in the one mount map they share.
_MOUNT_PREFIX = "django:"

# `path`/`url`/`re_path` are ordinary names, so the file has to prove they are
# Django's before its calls become endpoints. Not a `urls.py` filename gate:
# `ModelAdmin.get_urls` builds a real URLconf from `options.py`, and Django's
# own admin publishes most of its surface that way.
_DJANGO_URLS_IMPORT = ("django.urls", "django.conf.urls")


def _module_suffixes(rel_path: str) -> list[str]:
    """Dotted modules ``rel_path`` could be imported as, longest first.

    ``include()`` names a module relative to whichever directory is on the path,
    which is not always the repo root, so the match is by suffix.
    """
    parts = rel_path.removesuffix(".py").split("/")
    return [".".join(parts[i:]) for i in range(len(parts))]


class DjangoDialect:
    name = "django"
    extensions = PYTHON

    def collect_mounts(self, content: str) -> dict[str, str]:
        """``include("api.urls")`` mounts declared in *content*, keyed by module."""
        if not any(mod in content for mod in _DJANGO_URLS_IMPORT):
            return {}
        return {_MOUNT_PREFIX + module: prefix for prefix, module in django_includes(content)}

    def extract(self, ctx: ScanContext) -> list[Contract]:
        if not any(mod in ctx.content for mod in _DJANGO_URLS_IMPORT):
            return []
        mount = next(
            (
                p
                for m in _module_suffixes(ctx.rel_path)
                if (p := ctx.mounts.get(_MOUNT_PREFIX + m)) is not None
            ),
            "",
        )
        out: list[Contract] = []
        for route in django_routes(ctx.content):
            c = build_provider_contract(
                ctx,
                method="*",
                path_raw=compose_prefix(mount, route.path or ""),
                framework="django",
                line=line_at(ctx.content, route.offset),
                # `login_required(view)` names the decorator, not the view.
                handler=None if route.handler_call else route.handler,
            )
            if c is not None:
                out.append(c)
        return out
