"""Topic dialects: a broker library's calls as a table, read through one resolver.

A :class:`TopicCall` names a call head (a regex ending at the opening
parenthesis), the role it plays, and where in the argument list the
destination name sits (:class:`..strings.Arg`). :class:`TopicDialect` reads
each call through :func:`..calls.call_sites` and resolves the name with the
file's constants, so a queue named by a constant folds exactly as a URL does.
A value that cannot be settled inside the file is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import (
    TOPIC_KIND_QUEUE,
    TOPIC_KIND_TOPIC,
    TOPIC_PATTERN,
    TOPIC_PATTERN_GLOB,
    TOPIC_PATTERN_NATS,
    TOPIC_PATTERN_REGEX,
    TOPIC_PREFIX,
    TOPIC_ROUTING_KEY_UNRESOLVED,
)

from ..base import line_at
from ..calls import call_sites
from ..dialect import build_contract

if TYPE_CHECKING:
    import re
    from collections.abc import Callable

    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext
    from ..calls import FileStrings
    from ..strings import Arg

#: Wildcard characters of each subscription-pattern syntax, which a name must
#: contain to be read as a pattern (``regex`` names are patterns outright).
_PATTERN_WILDCARDS = {TOPIC_PATTERN_NATS: "*>", TOPIC_PATTERN_GLOB: "*?[", TOPIC_PATTERN_REGEX: ""}


@dataclass(frozen=True)
class TopicCall:
    """One broker call shape.

    ``default_exchange`` marks a publish whose empty exchange name is the
    broker's default exchange, which delivers to the queue the routing key
    names. ``queue`` is the bound queue of a binding call. ``requires`` are
    substrings (the library's import) one of which the file must contain.
    ``normalize`` maps a resolved name to the broker's own (a queue URL to its
    last segment). ``pattern`` is the subscription-pattern syntax of a
    consumer whose name may carry wildcards.
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
    requires: tuple[str, ...] = ()
    normalize: Callable[[str], str | None] | None = None
    pattern: str = ""


def topic_contract(
    ctx: ScanContext,
    *,
    name: str,
    broker: str,
    kind: str,
    role: str,
    label: str,
    confidence: float,
    offset: int,
    **extra: str | bool,
) -> Contract:
    """One topic contract for destination *name*; *extra* goes into ``meta``."""
    meta: dict[str, str | bool] = {"topic": name, "broker": broker, "kind": kind, **extra}
    return build_contract(
        ctx,
        contract_type="topic",
        contract_id=f"{TOPIC_PREFIX}{name.lower()}",
        role=role,
        symbol_name=f"{label}('{name}')",
        confidence=confidence,
        line=line_at(ctx.content, offset),
        meta=meta,
    )


def call_contracts(
    ctx: ScanContext,
    broker: str,
    call: TopicCall,
    args: list[str],
    offset: int,
    strings: FileStrings,
) -> list[Contract]:
    """The contracts one call site of *call* yields."""
    keys, key_refused = (
        strings.resolve(args, call.routing_key) if call.routing_key else ([], False)
    )
    routing_key = keys[0] if keys else ""
    queues: list[str] = []
    if call.queue is not None:
        queues, _ = strings.resolve(args, call.queue)
        if not queues:
            return []  # a binding with no readable queue binds nothing we can name
    names, _ = strings.resolve(args, call.name, call.normalize)
    out: list[Contract] = []
    for name in names:
        kind, key, unresolved = call.kind, routing_key, key_refused
        if not name and call.default_exchange:
            if not routing_key:
                continue  # the queue the default exchange delivers to is unknown
            name, kind, key, unresolved = routing_key, TOPIC_KIND_QUEUE, "", False
        if not name:
            continue
        extra: dict[str, str | bool] = {}
        if key:
            extra["routing_key"] = key
        if unresolved:
            extra[TOPIC_ROUTING_KEY_UNRESOLVED] = True
        if queues:
            extra["queue"] = queues[0]
        if call.pattern and (
            not _PATTERN_WILDCARDS[call.pattern]
            or any(ch in name for ch in _PATTERN_WILDCARDS[call.pattern])
        ):
            extra[TOPIC_PATTERN] = call.pattern
        out.append(
            topic_contract(
                ctx,
                name=name,
                broker=broker,
                kind=kind,
                role=call.role,
                label=call.label,
                confidence=call.confidence,
                offset=offset,
                **extra,
            )
        )
    return out


class TopicDialect:
    """One broker library's calls, for the files whose extension its calls read."""

    def __init__(self, name: str, calls: tuple[TopicCall, ...]) -> None:
        self.name = name
        self.calls = calls
        self.extensions = frozenset().union(*(c.extensions for c in calls))

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for call, args, m, strings in call_sites(ctx, self.calls):
            out.extend(call_contracts(ctx, self.name, call, args, m.start(), strings))
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


def last_segment(sep: str) -> Callable[[str], str | None]:
    """A normalizer keeping what follows the last *sep* (a queue URL's name, an ARN's)."""

    def normalize(text: str) -> str | None:
        tail = text.rstrip(sep).rpartition(sep)[2]
        return tail or None

    return normalize


__all__ = [
    "TopicCall",
    "TopicDialect",
    "last_segment",
    "topic_contract",
    "topic_identity",
]
