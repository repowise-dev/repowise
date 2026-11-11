"""Topic dialects: a broker library's calls as a table, read through one resolver.

A :class:`TopicCall` names a call head (a regex ending at the opening
parenthesis), the role it plays, and where in the argument list the
destination name sits (:class:`..strings.Arg`). :class:`TopicDialect` reads
each call through :func:`..calls.call_sites` and resolves the name with the
file's constants, so a queue named by a constant folds exactly as a URL does.
A value that cannot be settled inside the file is refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
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
from repowise.core.workspace.origins import ContractOrigin

from ..base import line_at
from ..calls import call_sites, declared_receivers
from ..dialect import build_contract, rows_gate

if TYPE_CHECKING:
    from collections.abc import Callable

    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext
    from ..calls import FileStrings
    from ..strings import Arg
    from .declared import QueueDeclaration

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
    consumer whose name may carry wildcards. ``declared`` reads a queue
    named through its declaration's result (``q.Name``): the ``queue`` of a
    binding, else the ``name``.
    """

    head: re.Pattern[str]
    role: str
    label: str
    extensions: frozenset[str]
    name: Arg
    kind: str = TOPIC_KIND_TOPIC
    origin: ContractOrigin = ContractOrigin.CLIENT_CALL
    routing_key: Arg | None = None
    queue: Arg | None = None
    declared: QueueDeclaration | None = None
    default_exchange: bool = False
    requires: tuple[str, ...] = ()
    normalize: Callable[[str], str | None] | None = None
    pattern: str = ""


@dataclass(frozen=True)
class ReceiverCall:
    """A broker call read on any name the file declares with one of the client's types.

    ``send`` and ``produce`` are too common to read on any receiver, and the
    receiver's name is the author's choice (``KafkaTemplate<K, V> kt``), so the
    file's own declarations of *types* (:func:`..calls.declared_receivers`)
    name the receivers, and ``verbs`` (a regex alternation) are read on those
    alone. The other fields are a :class:`TopicCall`'s.
    """

    types: re.Pattern[str]
    verbs: str
    role: str
    label: str
    extensions: frozenset[str]
    name: Arg
    requires: tuple[str, ...]
    origin: ContractOrigin = ContractOrigin.CLIENT_CALL


@lru_cache(maxsize=256)
def _receiver_call(row: ReceiverCall, receivers: tuple[str, ...]) -> TopicCall:
    alts = "|".join(re.escape(r) for r in receivers)
    head = re.compile(
        rf"(?<![\w$.])(?:(?:this|self)\s*\.\s*)?(?:{alts})\s*\.\s*(?:{row.verbs})\s*\("
    )
    return TopicCall(
        head=head,
        role=row.role,
        label=row.label,
        extensions=row.extensions,
        name=row.name,
        origin=row.origin,
        requires=row.requires,
    )


def topic_contract(
    ctx: ScanContext,
    *,
    name: str,
    broker: str,
    kind: str,
    role: str,
    label: str,
    origin: ContractOrigin,
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
        origin=origin,
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
    site: int,
) -> list[Contract]:
    """The contracts one call site of *call* yields; *offset* is where its head starts.

    A call whose name is refused is recorded by *site*, where its argument list
    opens, which every dialect reading the call shares wherever its head begins.
    """
    keys, key_refused = (
        strings.resolve(args, call.routing_key) if call.routing_key else ([], False)
    )
    routing_key = keys[0] if keys else ""
    queues: list[str] = []
    if call.queue is not None:
        queues, _ = strings.resolve(args, call.queue)
        if not queues and call.declared is not None:
            queues = call.declared.queues(ctx, args, call.queue, strings)
        if not queues:
            return []  # a binding with no readable queue binds nothing we can name
    names, refused = strings.resolve(args, call.name, call.normalize)
    if not names and call.declared is not None and call.queue is None:
        names = call.declared.queues(ctx, args, call.name, strings)
    if refused and not names:
        ctx.note_unresolved(site)
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
                origin=call.origin,
                offset=offset,
                **extra,
            )
        )
    return out


class TopicDialect:
    """One broker library's calls, for the files whose extension its calls read."""

    def __init__(
        self,
        name: str,
        calls: tuple[TopicCall, ...],
        receivers: tuple[ReceiverCall, ...] = (),
    ) -> None:
        self.name = name
        self.calls = calls
        self.receivers = receivers
        rows = (*receivers, *calls)
        self.extensions = frozenset().union(*(c.extensions for c in rows))
        self.requires = rows_gate(rows)

    def _typed_calls(self, ctx: ScanContext) -> list[TopicCall]:
        """A :class:`TopicCall` per receiver row, on the names this file declares."""
        out: list[TopicCall] = []
        for row in self.receivers:
            if ctx.suffix not in row.extensions or not ctx.has_any(row.requires):
                continue
            names = declared_receivers(ctx.content, row.types)
            if names:
                out.append(_receiver_call(row, tuple(sorted(names))))
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        # Typed rows first: a call both read is kept with the typed row's origin.
        calls = [*self._typed_calls(ctx), *self.calls]
        for call, args, m, strings in call_sites(ctx, calls):
            out.extend(call_contracts(ctx, self.name, call, args, m.start(), strings, m.end() - 1))
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
    "ReceiverCall",
    "TopicCall",
    "TopicDialect",
    "last_segment",
    "topic_contract",
    "topic_identity",
]
