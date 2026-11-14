"""Django REST framework HTTP provider dialect: ``router.register(r"users", UserViewSet)``.

A router publishes a list route (``users/``) and a detail route (``users/{pk}/``)
per registration. Neither names a method, so both are recorded as ``*``, as
:class:`.django.DjangoDialect` records a ``path()`` entry.

The real path is the registration's prefix under the mount the router's
``urls`` reach a URLconf at: ``path("api/", include(router.urls))`` in the same
file, ``urlpatterns += router.urls`` (no prefix), and the cross-file
``include("app.urls")`` mount the Django dialect collects for the module.

Ceilings: a router imported from another module and included there is read with
no in-file mount; ``@action`` routes, format suffixes and nested routers add
paths this does not publish.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from repowise.core.ingestion.framework_routes import django_includes

from ..base import line_at
from ..calls import outside_comments
from ..langs import PYTHON
from .dialect import build_provider_contract
from .django import _MOUNT_PREFIX, _module_suffixes
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# ``router = DefaultRouter()``, ``r = routers.SimpleRouter(trailing_slash=False)``.
_ROUTER_RE = re.compile(
    r"^[ \t]*(?P<var>[A-Za-z_]\w*)\s*=\s*(?:\w+\.)?\w*(?:Default|Simple)Router\s*\(",
    re.MULTILINE,
)

# ``router.register(r"users", UserViewSet)``: the registration's prefix literal.
_REGISTER_RE = re.compile(
    r"""(?<![\w.])(?P<var>[A-Za-z_]\w*)\s*\.\s*register\s*\(\s*(?:r|u)?(?P<q>['"])(?P<prefix>[^'"]*)(?P=q)"""
)

# ``urlpatterns += router.urls`` and ``urlpatterns = router.urls``: no prefix.
_BARE_URLS_RE = re.compile(r"urlpatterns\s*(?:\+)?=\s*(?P<var>[A-Za-z_]\w*)\.urls\b")


class _Registration(NamedTuple):
    var: str
    prefix: str
    offset: int


def _routers(content: str) -> set[str]:
    return {m.group("var") for m in _ROUTER_RE.finditer(content)}


def _registrations(content: str, routers: set[str]) -> list[_Registration]:
    return [
        _Registration(m.group("var"), m.group("prefix").strip("^$/"), m.start())
        for m in _REGISTER_RE.finditer(content)
        if m.group("var") in routers
    ]


def _router_mounts(content: str, routers: set[str]) -> dict[str, str]:
    """``{router variable: prefix}`` where this file's URLconf includes ``<router>.urls``."""
    mounts = {m.group("var"): "" for m in _BARE_URLS_RE.finditer(content)}
    for prefix, expr in django_includes(content):
        var, _, attribute = expr.rpartition(".")
        if attribute == "urls" and var in routers:
            mounts[var] = prefix
    return mounts


class DrfDialect:
    name = "drf"
    extensions = PYTHON
    requires = ("rest_framework",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        routers = _routers(content)
        if not routers:
            return []
        mounts = _router_mounts(content, routers)
        file_mount = next(
            (
                p
                for m in _module_suffixes(ctx.rel_path)
                if (p := ctx.mounts.get(_MOUNT_PREFIX + m)) is not None
            ),
            "",
        )
        out: list[Contract] = []
        for reg in outside_comments(ctx, _registrations(content, routers)):
            base = compose_prefix(file_mount, compose_prefix(mounts.get(reg.var, ""), reg.prefix))
            for tail in ("", "{pk}"):
                c = build_provider_contract(
                    ctx,
                    method="*",
                    path_raw=compose_prefix(base, tail) if tail else base,
                    framework="drf",
                    line=line_at(content, reg.offset),
                )
                if c is not None:
                    out.append(c)
        return out
