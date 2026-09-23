"""Topic dialects: a broker library's calls as a table, read through one resolver.

A :class:`TopicCall` names a call head (a regex ending at the opening
parenthesis), the role it plays, and where in the argument list the
destination name sits (:class:`..strings.Arg`). :class:`TopicDialect` reads
the argument list with the shared bracket scanner and resolves each value
through :func:`..strings.resolve_argument`, so a queue named by a constant
folds exactly as a URL does. A value that cannot be settled inside the file is
refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import (
    TOPIC_KIND_QUEUE,
    TOPIC_KIND_TOPIC,
    TOPIC_ROUTING_KEY_UNRESOLVED,
)

from ..base import line_at
from ..dialect import build_contract
from ..strings import call_arguments, resolve_argument, string_constants, syntax_for_suffix

if TYPE_CHECKING:
    import re

    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext
    from ..strings import Arg, StringSyntax


@dataclass(frozen=True)
class TopicCall:
    """One broker call shape.

    ``default_exchange`` marks a publish whose empty exchange name is the
    broker's default exchange, which delivers to the queue the routing key
    names. ``queue`` is the bound queue of a binding call.
    """

    head: re.Pattern[str]
    role: str
    label: str
    extensions: frozenset[str]
    name: Arg
    kind: str = TOPIC_KIND_TOPIC
    confidence: float = 0.8
    routing_key: Arg | None = None
    queue: Arg | None = None
    default_exchange: bool = False


class TopicDialect:
    """One broker library's calls, for the files whose extension its calls read."""

    def __init__(self, name: str, calls: tuple[TopicCall, ...]) -> None:
        self.name = name
        self.calls = calls
        self.extensions = frozenset().union(*(c.extensions for c in calls))

    def extract(self, ctx: ScanContext) -> list[Contract]:
        syntax = syntax_for_suffix(ctx.suffix)
        if syntax is None:
            return []
        content = ctx.content
        constants: dict[str, str] | None = None
        out: list[Contract] = []
        for call in self.calls:
            if ctx.suffix not in call.extensions:
                continue
            for m in call.head.finditer(content):
                args = call_arguments(content, m.end() - 1)
                if not args:
                    continue
                if constants is None:
                    constants = string_constants(content, syntax)
                out.extend(self._contracts(ctx, call, args, m.start(), syntax, constants))
        return out

    def _contracts(
        self,
        ctx: ScanContext,
        call: TopicCall,
        args: list[str],
        offset: int,
        syntax: StringSyntax,
        constants: dict[str, str],
    ) -> list[Contract]:
        keys, key_refused = (
            resolve_argument(args, call.routing_key, syntax, constants)
            if call.routing_key
            else ([], False)
        )
        routing_key = keys[0] if keys else ""
        queues: list[str] = []
        if call.queue is not None:
            queues, _ = resolve_argument(args, call.queue, syntax, constants)
            if not queues:
                return []  # a binding with no readable queue binds nothing we can name
        names, _ = resolve_argument(args, call.name, syntax, constants)
        out: list[Contract] = []
        for name in names:
            kind, key, unresolved = call.kind, routing_key, key_refused
            if not name and call.default_exchange:
                if not routing_key:
                    continue  # the queue the default exchange delivers to is unknown
                name, kind, key, unresolved = routing_key, TOPIC_KIND_QUEUE, "", False
            if not name:
                continue
            meta: dict[str, str | bool] = {"topic": name, "broker": self.name, "kind": kind}
            if key:
                meta["routing_key"] = key
            if unresolved:
                meta[TOPIC_ROUTING_KEY_UNRESOLVED] = True
            if queues:
                meta["queue"] = queues[0]
            out.append(
                build_contract(
                    ctx,
                    contract_type="topic",
                    contract_id=f"topic::{name.lower()}",
                    role=call.role,
                    symbol_name=f"{call.label}('{name}')",
                    confidence=call.confidence,
                    line=line_at(ctx.content, offset),
                    meta=meta,
                )
            )
        return out


def topic_identity(c: Contract) -> tuple[str, ...]:
    """One contract per file, id, role and route: a second key or bound queue is new."""
    meta = c.meta
    return (
        c.file_path,
        c.contract_id,
        c.role,
        meta.get("routing_key", ""),
        meta.get("queue", ""),
    )


__all__ = ["TopicCall", "TopicDialect", "topic_identity"]
