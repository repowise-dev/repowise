"""Socket dialects: a transport's endpoints, connections and events as call tables.

Two identities name a socket contract. An endpoint or a connection is its path,
normalized exactly as an HTTP path is, so ``socket::/hubs/game`` links a hub
mapped at ``/hubs/game`` to a client connecting to ``wss://host/hubs/game``.
A message is its event within a scope, ``socket::<scope>#<event>``: the scope
is a socket.io namespace (``/`` by default) or a broadcast channel
(``private-orders.{param}``), and the side that emits it is the provider.

A :class:`SocketCall` names a call head, where the value sits
(:class:`..strings.Arg`) and the function that turns the resolved value into
an identity; :class:`SocketDialect` reads a table of them through
:func:`..calls.call_sites`, so a URL held in a constant folds as it does for
HTTP. Dialects whose identity needs more than one call (a namespace, a
channel and its event) build contracts with :func:`event_contract`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..base import line_at
from ..calls import call_sites
from ..dialect import build_contract
from ..http.paths import (
    extract_path_from_url,
    is_unusable_consumer_path,
    normalize_http_path,
    strip_leading_base_expr,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext
    from ..strings import Arg

_HOLE_RE = re.compile(r"\$\{[^{}]*\}")


def path_identity(text: str) -> str | None:
    """The stable path identity of a resolved socket URL, or ``None``."""
    value = text.strip()
    if not value or "/" not in value:
        return None
    path = extract_path_from_url(value)
    path, _base_token = strip_leading_base_expr(path)
    norm = normalize_http_path(path)
    if norm in ("", "/") or is_unusable_consumer_path(norm):
        return None
    return norm


def channel_scope(text: str) -> str | None:
    """A channel name with each interpolated part as ``{param}``; ``None`` when nothing is fixed."""
    scope = _HOLE_RE.sub("{param}", text.strip())
    return scope if scope.replace("{param}", "").strip(".-_:") else None


@dataclass(frozen=True)
class SocketCall:
    """One endpoint, connect or event call shape.

    ``normalize`` maps the resolved value to the contract's identity: a URL to
    its path for an endpoint, a channel with ``{param}`` holes for an event.
    """

    head: re.Pattern[str]
    role: str
    transport: str
    label: str
    extensions: frozenset[str]
    name: Arg
    confidence: float = 0.8
    requires: tuple[str, ...] = ()
    normalize: Callable[[str], str | None] = path_identity


def _socket_contract(
    ctx: ScanContext,
    *,
    contract_id: str,
    role: str,
    symbol: str,
    confidence: float,
    offset: int,
    meta: dict[str, str],
) -> Contract:
    return build_contract(
        ctx,
        contract_type="socket",
        contract_id=f"socket::{contract_id}",
        role=role,
        symbol_name=symbol,
        confidence=confidence,
        line=line_at(ctx.content, offset),
        meta=meta,
    )


def event_contract(
    ctx: ScanContext,
    *,
    scope: str,
    event: str,
    role: str,
    transport: str,
    label: str,
    confidence: float,
    offset: int,
) -> Contract:
    """One message: *event* within *scope*, emitted by a provider, received by a consumer."""
    return _socket_contract(
        ctx,
        contract_id=f"{scope}#{event}",
        role=role,
        symbol=f"{label}('{event}')",
        confidence=confidence,
        offset=offset,
        meta={"scope": scope, "event": event, "transport": transport},
    )


def event_contracts(
    ctx: ScanContext,
    call: SocketCall,
    scopes: list[str],
    events: list[str],
    offset: int,
    label: str | None = None,
) -> list[Contract]:
    """One contract per scope and event of one call site of *call*."""
    return [
        event_contract(
            ctx,
            scope=scope,
            event=event,
            role=call.role,
            transport=call.transport,
            label=label or call.label,
            confidence=call.confidence,
            offset=offset,
        )
        for scope in scopes
        for event in events
    ]


class SocketDialect:
    """One socket library's endpoint and connect calls."""

    def __init__(self, name: str, calls: tuple[SocketCall, ...]) -> None:
        self.name = name
        self.calls = calls
        self.extensions = frozenset().union(*(c.extensions for c in calls))

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for call, args, m, strings in call_sites(ctx, self.calls):
            paths, _ = strings.resolve(args, call.name, call.normalize)
            out.extend(
                _socket_contract(
                    ctx,
                    contract_id=path,
                    role=call.role,
                    symbol=f"{call.label}('{path}')",
                    confidence=call.confidence,
                    offset=m.start(),
                    meta={"path": path, "transport": call.transport},
                )
                for path in paths
            )
        return out


__all__ = [
    "SocketCall",
    "SocketDialect",
    "channel_scope",
    "event_contract",
    "event_contracts",
    "path_identity",
]
