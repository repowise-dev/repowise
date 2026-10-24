"""Refit typed clients: an interface whose methods carry the request as attributes.

``[Get("/api/catalog/items/{id}")] Task<Item> GetItem(int id);`` on an
interface is a call to that route each time the generated client method is
invoked, so the attribute is the consumer. The interface's base comes from its
registration (``AddRefitClient<ICatalogApi>().ConfigureHttpClient(c =>
c.BaseAddress = ...)`` or ``RestService.For<ICatalogApi>(url)``), which the C#
HTTP dialect collects into the mounts (:mod:`.csharp_clients`).
"""

from __future__ import annotations

import re
from functools import partial
from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..langs import CSHARP
from ..strings import CSHARP_SYNTAX
from .client_calls import ClientCallMatch, consumer_contracts
from .csharp_clients import TYPED_CLIENT_KEY, client_base, type_at
from .csharp_http import _FileNames

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# `[Get("/path")]`, `[Post(@"/path")]`; the attribute's whole argument is the path.
_ATTRIBUTE_RE = re.compile(
    r"\[\s*(?:Refit\s*\.\s*)?(Get|Post|Put|Delete|Patch|Head|Options)\s*\(\s*(@?\"[^\"\n]*\")\s*\)"
)
_REFIT_RE = re.compile(r"\busing\s+Refit\s*;|\bRefit\s*\.")


def _under_base(base: str, path: str) -> str:
    """Refit keeps the base's own path: ``http://h/api`` + ``/items`` is ``http://h/api/items``."""
    return base.rstrip("/") + "/" + path.lstrip("/")


class RefitDialect:
    name = "refit"
    extensions = CSHARP
    requires = ("Refit",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        # `Refit` alone could be a word in a comment or another library's name.
        if _REFIT_RE.search(content) is None:
            return []
        declared = _FileNames(content).types
        matches: list[ClientCallMatch] = []
        for m in _ATTRIBUTE_RE.finditer(content):
            owner = type_at(declared, m.start())
            if owner is None:
                continue
            base = client_base(ctx.mounts, TYPED_CLIENT_KEY + owner[0])
            matches.append(
                ClientCallMatch(
                    client="refit",
                    url=m.group(2),
                    offset=m.start(),
                    method=m.group(1).upper(),
                    origin=ContractOrigin.CLIENT_CALL,
                    compose=partial(_under_base, base) if base is not None else None,
                )
            )
        return consumer_contracts(ctx, matches, CSHARP_SYNTAX, path_only=True)
