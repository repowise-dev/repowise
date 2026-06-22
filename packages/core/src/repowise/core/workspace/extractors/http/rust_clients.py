"""Rust HTTP consumer dialect — ``reqwest`` client calls.

Recognises ``reqwest`` method calls whose URL is recoverable at the call site:

* string literals — ``client.get("http://host/path")`` / ``reqwest::get("...")``;
* ``format!`` templates — ``client.get(format!("{}/systems/{}", base, id))``,
  where the leading ``{}`` is the base placeholder and interior ``{}`` collapse
  to ``{param}``.

Calls with a bare variable URL (``client.get(&url)``) are left unmatched. Hyper
is lower level — its method is set apart from the URI — and is not modelled.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import RUST
from .dialect import build_consumer_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_VERBS = r"get|post|put|delete|patch|head"

# client.get("...") / .post(&"...") — receiver method with a string literal.
_METHOD_LIT_RE = re.compile(rf"""\.({_VERBS})\s*\(\s*&?\s*"([^"]*)\"""")
# reqwest::get("...") — free function form.
_FREE_LIT_RE = re.compile(rf"""\breqwest::({_VERBS})\s*\(\s*&?\s*"([^"]*)\"""")
# client.get(format!("{}/path", ...)) — receiver method with a format! template.
_METHOD_FMT_RE = re.compile(rf"""\.({_VERBS})\s*\(\s*&?\s*format!\s*\(\s*"([^"]*)\"""")
# reqwest::get(format!("...", ...))
_FREE_FMT_RE = re.compile(rf"""\breqwest::({_VERBS})\s*\(\s*&?\s*format!\s*\(\s*"([^"]*)\"""")

# A Rust format! placeholder: `{}`, `{0}`, `{name}`. Rewritten to the `${expr}`
# template form the shared path helpers understand (leading one stripped as the
# base, interior ones collapsed to {param}).
_FMT_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")


class RustClientsDialect:
    name = "rust-clients"
    extensions = RUST

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        out: list[Contract] = []

        def emit(method: str, url: str) -> None:
            if "/" not in url:
                return  # Not a URL path — skip map/collection .get("key") calls.
            c = build_consumer_contract(
                ctx, method=method.upper(), url=url, client="reqwest", confidence=0.65
            )
            if c is not None:
                out.append(c)

        for rx in (_METHOD_LIT_RE, _FREE_LIT_RE):
            for m in rx.finditer(content):
                emit(m.group(1), m.group(2))

        for rx in (_METHOD_FMT_RE, _FREE_FMT_RE):
            for m in rx.finditer(content):
                url = _FMT_PLACEHOLDER_RE.sub("${x}", m.group(2))
                emit(m.group(1), url)

        return out
