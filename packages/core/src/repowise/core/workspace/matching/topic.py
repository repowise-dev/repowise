"""Topic matching: routing keys, and queues reached through an exchange binding.

A message broker's producer and consumer often name different things. A
RabbitMQ producer publishes to an *exchange* with a routing key; a consumer
reads a *queue*; a binding (``bindQueue(queue, exchange, pattern)``) is what
connects the two, and it can live in either service or a third. Extraction
records a binding as a consumer of the exchange carrying ``queue`` and
``routing_key`` in its meta, so the exact pass already links the binding site.
The pass here links each consumer of the bound queue to the exchange's
producers, and the routing check keeps a binding from linking to a producer
whose key it would never receive.
"""

from __future__ import annotations

from collections import defaultdict
from functools import cache
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import normalize_contract_id
from repowise.core.workspace.extractors.topic.dialect import KIND_BINDING, KIND_QUEUE

from .common import internal

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from .common import MatchState


@cache
def _words_match(pattern: tuple[str, ...], key: tuple[str, ...]) -> bool:
    """AMQP topic matching over dot-separated words: ``*`` is one, ``#`` is zero or more."""
    if not pattern:
        return not key
    head, rest = pattern[0], pattern[1:]
    if head == "#":
        return _words_match(rest, key) or (bool(key) and _words_match(pattern, key[1:]))
    if not key:
        return False
    return head in ("*", key[0]) and _words_match(rest, key[1:])


def routing_matches(pattern: str, key: str) -> bool:
    """True when a message published with *key* reaches a binding on *pattern*.

    Topic-exchange semantics, which reduce to equality for a pattern with no
    wildcard (a direct exchange). An empty side is a fanout binding or a
    publish whose key nothing inspects, so it matches everything.
    """
    if not pattern or not key:
        return True
    return _words_match(tuple(pattern.split(".")), tuple(key.split(".")))


def accepts(provider: Contract, consumer: Contract) -> bool:
    """Whether the exact pass may link *consumer* to *provider* on routing grounds."""
    return routing_matches(
        consumer.meta.get("routing_key", ""), provider.meta.get("routing_key", "")
    )


def binding_pass(state: MatchState) -> None:
    """Link each unmatched queue consumer to the producers of an exchange its queue is bound to."""
    bindings: dict[str, list[Contract]] = defaultdict(list)
    for c in state.consumers:
        if c.contract_type == "topic" and c.meta.get("kind") == KIND_BINDING:
            queue = c.meta.get("queue")
            if queue:
                bindings[queue.lower()].append(c)
    if not bindings:
        return

    for consumer in state.unmatched("topic"):
        if consumer.meta.get("kind") != KIND_QUEUE:
            continue
        for binding in bindings.get(str(consumer.meta.get("topic", "")).lower(), []):
            for provider in state.provider_index.get(normalize_contract_id(binding.contract_id), []):
                if internal(provider, consumer) or not accepts(provider, binding):
                    continue
                state.add(
                    consumer,
                    provider,
                    "exact",
                    min(provider.confidence, consumer.confidence, binding.confidence),
                    via_alias=True,
                )


__all__ = ["accepts", "binding_pass", "routing_matches"]
