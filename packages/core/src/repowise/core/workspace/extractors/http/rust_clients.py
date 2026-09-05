"""Rust HTTP consumer dialect: ``reqwest`` client calls.

Recognises ``reqwest`` method calls whose URL is recoverable at the call site:

* string literals: ``client.get("http://host/path")`` / ``reqwest::get("...")``;
* ``format!`` templates: ``client.get(format!("{}/systems/{}", base, id))``,
  where the leading ``{}`` is the base placeholder and interior ``{}`` collapse
  to ``{param}``.

Calls with a bare variable URL (``client.get(&url)``) are left unmatched. Hyper
is lower level, its method is set apart from the URI, and is not modelled.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..langs import RUST
from .client_calls import RUST_SYNTAX, ClientCallMatch, consumer_contracts, matches_in

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_VERBS = r"get|post|put|delete|patch|head"

# client.get("...") / .post(&"...") — receiver method with a string literal.
_METHOD_LIT_RE = re.compile(rf"""\.({_VERBS})\s*\(\s*&?\s*"([^"]*)\"""")
# reqwest::get("...") — free function form.
_FREE_LIT_RE = re.compile(rf"""\breqwest::({_VERBS})\s*\(\s*&?\s*"([^"]*)\"""")
# client.get(format!("{}/path", ...)) — receiver method with a format! template.
_METHOD_FMT_RE = re.compile(rf"""\.({_VERBS})\s*\(\s*&?\s*(format!\s*\(\s*"[^"]*")""")
# reqwest::get(format!("...", ...))
_FREE_FMT_RE = re.compile(rf"""\breqwest::({_VERBS})\s*\(\s*&?\s*(format!\s*\(\s*"[^"]*")""")

_CONFIDENCE = 0.65


def reqwest_calls(content: str) -> Iterator[ClientCallMatch]:
    for rx in (_METHOD_LIT_RE, _FREE_LIT_RE):
        yield from matches_in(
            content, rx, client="reqwest", url_group=2, method_group=1, confidence=_CONFIDENCE
        )
    for rx in (_METHOD_FMT_RE, _FREE_FMT_RE):
        for m in rx.finditer(content):
            # Only the template is read; the closing paren completes the call
            # expression for the resolver without scanning the arguments.
            yield ClientCallMatch(
                client="reqwest",
                url=m.group(2) + ")",
                offset=m.start(),
                method=m.group(1).upper(),
                confidence=_CONFIDENCE,
            )


class RustClientsDialect:
    name = "rust-clients"
    extensions = RUST

    def extract(self, ctx: ScanContext) -> list[Contract]:
        # `.get("key")` on a map has no slash; the receiver is anyone's.
        return consumer_contracts(ctx, reqwest_calls(ctx.content), RUST_SYNTAX, path_only=True)
