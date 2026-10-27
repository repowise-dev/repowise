"""Sinatra (Ruby) HTTP provider dialect — ``get '/path' do ... end``.

Recognises Sinatra routes declared top-level or within ``Sinatra::Base`` /
``Sinatra::Application`` classes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..base import line_at
from ..calls import code_matches
from ..langs import RUBY
from .dialect import build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_SINATRA_ROUTE_RE = re.compile(
    r"\b(?P<verb>get|post|put|patch|delete|options|head)\s*"
    r"(?:\(\s*)?"
    r"(?:[\"'](?P<path>[^\"']+)[\"']|:(?P<sym>\w+))"
    r"(?:\s*,\s*[^do{\n]+)?"
    r"\s*\)?"
    r"\s*(?:do|\{)"
)


class SinatraDialect:
    name = "sinatra"
    extensions = RUBY
    requires = ("sinatra", "Sinatra::")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in code_matches(ctx, _SINATRA_ROUTE_RE):
            verb = m.group("verb").upper()
            path = m.group("path") or f"/{m.group('sym')}"
            c = build_provider_contract(
                ctx,
                method=verb,
                path_raw=path,
                framework="sinatra",
                line=line_at(ctx.content, m.start()),
            )
            if c is not None:
                out.append(c)
        return out
