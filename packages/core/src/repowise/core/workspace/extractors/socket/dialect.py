"""Socket dialects: a transport's endpoint and connect calls as a pattern table.

A socket contract is identified by its path, normalized exactly as an HTTP path
is, so ``socket::/hubs/game`` links a hub mapped at ``/hubs/game`` to a client
connecting to ``wss://host/hubs/game``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..base import line_at
from ..dialect import build_contract
from ..http.paths import (
    extract_path_from_url,
    is_unusable_consumer_path,
    normalize_http_path,
    strip_leading_base_expr,
)

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

#: A C# string literal, capturing its ``$``/``@`` prefix and its body.
CSHARP_STRING = r"""(\$?@?)"([^"]*)\""""


@dataclass(frozen=True)
class SocketPattern:
    """One endpoint or connect shape.

    ``context`` must appear somewhere in the file for the pattern to run, which
    is how two libraries spelling ``new WebSocket("...")`` are told apart.
    """

    regex: re.Pattern[str]
    role: str
    transport: str
    confidence: float
    label: str
    identity_group: int
    prefix_group: int | None = None
    context: re.Pattern[str] | None = None


def _to_template(prefix: str, text: str) -> str:
    """Rewrite a C# interpolated string body into ``${expr}`` template form."""
    if "$" in prefix:
        return text.replace("{", "${")
    return text


def socket_identity(raw: str, prefix: str = "") -> str | None:
    """The stable path identity of a socket URL-like string, or ``None``."""
    value = _to_template(prefix, raw).strip()
    if not value or "/" not in value:
        return None
    path = extract_path_from_url(value)
    path, _base_token = strip_leading_base_expr(path)
    norm = normalize_http_path(path)
    if norm in ("", "/") or is_unusable_consumer_path(norm):
        return None
    return norm


class SocketDialect:
    """One socket library's patterns, for the given file extensions."""

    def __init__(
        self, name: str, extensions: frozenset[str], patterns: tuple[SocketPattern, ...]
    ) -> None:
        self.name = name
        self.extensions = extensions
        self.patterns = patterns

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        out: list[Contract] = []
        for p in self.patterns:
            if p.context is not None and not p.context.search(content):
                continue
            for m in p.regex.finditer(content):
                prefix = m.group(p.prefix_group) if p.prefix_group else ""
                identity = socket_identity(m.group(p.identity_group), prefix)
                if identity is None:
                    continue
                out.append(
                    build_contract(
                        ctx,
                        contract_type="socket",
                        contract_id=f"socket::{identity}",
                        role=p.role,
                        symbol_name=f"{p.label}('{identity}')",
                        confidence=p.confidence,
                        line=line_at(content, m.start()),
                        meta={"path": identity, "transport": p.transport},
                    )
                )
        return out


__all__ = ["CSHARP_STRING", "SocketDialect", "SocketPattern", "socket_identity"]
