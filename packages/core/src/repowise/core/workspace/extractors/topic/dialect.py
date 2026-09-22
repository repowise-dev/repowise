"""Topic dialects: a broker library's calls as a table, read through one resolver.

A :class:`TopicCall` names a call head (a regex ending at the opening
parenthesis), the role it plays, and where in the argument list the
destination name sits (:class:`Arg`). :class:`TopicDialect` reads the argument
list with the shared bracket scanner and resolves each value through
:func:`..strings.resolve_string`, so a queue named by a constant folds exactly
as a URL does. An argument that cannot be settled inside the file is refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..base import line_at
from ..dialect import build_contract
from ..strings import (
    call_arguments,
    resolve_string,
    split_top_level,
    string_constants,
    syntax_for_suffix,
)

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext
    from ..strings import StringSyntax

#: The destination a call names. A queue is read by exactly one consumer
#: group; a topic or subject fans out; an exchange routes to bound queues; a
#: binding is a queue subscribing to an exchange.
KIND_TOPIC = "topic"
KIND_QUEUE = "queue"
KIND_EXCHANGE = "exchange"
KIND_SUBJECT = "subject"
KIND_BINDING = "binding"


@dataclass(frozen=True)
class Arg:
    """Where a call carries a value: a keyword or object key first, else a position.

    ``keys`` match a keyword argument (``queue='jobs'``, ``topics = "a"``) or a
    key of an object-literal argument (``{ topic: 'a' }``). A value that is an
    array literal yields each element.
    """

    keys: tuple[str, ...] = ()
    pos: int | None = None


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
    kind: str = KIND_TOPIC
    confidence: float = 0.8
    routing_key: Arg | None = None
    queue: Arg | None = None
    default_exchange: bool = False


def _keyword_re(keys: tuple[str, ...]) -> re.Pattern[str]:
    alts = "|".join(re.escape(k) for k in keys)
    return re.compile(rf"""^['"]?(?:{alts})['"]?\s*[:=]\s*(?P<value>.+)$""", re.DOTALL)


def _entries(args: list[str]) -> list[str]:
    """The arguments, with each object-literal argument opened into its entries."""
    out: list[str] = []
    for a in args:
        if a.startswith("{") and a.endswith("}") and ":" in a:
            out.extend(e.strip() for e in split_top_level(a[1:-1], ","))
        else:
            out.append(a)
    return out


def _raw_values(args: list[str], arg: Arg) -> list[str]:
    """The source text of the value(s) *arg* selects from *args*."""
    value: str | None = None
    if arg.keys:
        pattern = _keyword_re(arg.keys)
        for entry in _entries(args):
            m = pattern.match(entry)
            if m is not None:
                value = m.group("value").strip()
                break
    if value is None and arg.pos is not None and arg.pos < len(args):
        value = args[arg.pos]
    if value is None:
        return []
    # An array literal: `['a', 'b']`, or a Java annotation's `{"a", "b"}`.
    if len(value) >= 2 and value[0] in "[{" and value[-1] in "]}" and ":" not in value:
        return [e.strip() for e in split_top_level(value[1:-1], ",") if e.strip()]
    return [value]


def _names(
    args: list[str], arg: Arg, syntax: StringSyntax, constants: dict[str, str]
) -> list[str]:
    """Each value *arg* selects, resolved; unresolvable or templated values are refused."""
    out: list[str] = []
    for raw in _raw_values(args, arg):
        name = resolve_string(raw, syntax, constants)
        if name is not None and "${" not in name:
            out.append(name.strip())
    return out


class TopicDialect:
    """One broker library's calls, for the files whose extension its calls read."""

    def __init__(
        self,
        name: str,
        broker: str,
        calls: tuple[TopicCall, ...],
        gate: re.Pattern[str] | None = None,
    ) -> None:
        self.name = name
        self.broker = broker
        self.calls = calls
        self.gate = gate
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
        keys = _names(args, call.routing_key, syntax, constants) if call.routing_key else []
        routing_key = keys[0] if keys else ""
        queues = _names(args, call.queue, syntax, constants) if call.queue else []
        if call.queue is not None and not queues:
            return []  # a binding with no readable queue binds nothing we can name
        out: list[Contract] = []
        for name in _names(args, call.name, syntax, constants):
            kind = call.kind
            key = routing_key
            if not name and call.default_exchange and routing_key:
                name, kind, key = routing_key, KIND_QUEUE, ""
            if not name:
                continue
            meta: dict[str, str] = {"topic": name, "broker": self.broker, "kind": kind}
            if key:
                meta["routing_key"] = key
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


__all__ = [
    "KIND_BINDING",
    "KIND_EXCHANGE",
    "KIND_QUEUE",
    "KIND_SUBJECT",
    "KIND_TOPIC",
    "Arg",
    "TopicCall",
    "TopicDialect",
    "topic_identity",
]
