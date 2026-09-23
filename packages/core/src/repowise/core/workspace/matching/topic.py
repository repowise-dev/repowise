"""Topic matching: routing keys, and queues reached through an exchange binding.

A message broker's producer and consumer often name different things. A
RabbitMQ producer publishes to an *exchange* with a routing key; a consumer
reads a *queue*; a binding (``bindQueue(queue, exchange, pattern)``) is what
connects the two, and it can live in either service or a third. Extraction
records a binding as a consumer of the exchange carrying ``queue`` and
``routing_key``. A binding is wiring, not a call: the exact pass leaves it
alone, and the pass here links each consumer of the bound queue to the
exchange's publishers, or the binding site itself when no consumer of the
queue was found. The routing check keeps a binding from linking to a publisher
whose key it would never receive.
"""

from __future__ import annotations

from collections import defaultdict
from functools import cache
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import (
    TOPIC_KIND_BINDING,
    TOPIC_KIND_QUEUE,
    TOPIC_ROUTING_KEY_UNRESOLVED,
    normalize_contract_id,
)

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


def is_binding(consumer: Contract) -> bool:
    """A binding row is linked by :func:`binding_pass`, never by the exact pass."""
    return consumer.meta.get("kind") == TOPIC_KIND_BINDING


def accepts(provider: Contract, consumer: Contract) -> bool:
    """Whether *consumer* receives what *provider* publishes, on routing grounds.

    A key the source did not settle routes nowhere: reading it as match-all
    would link every binding on the exchange.
    """
    if provider.meta.get(TOPIC_ROUTING_KEY_UNRESOLVED) or consumer.meta.get(
        TOPIC_ROUTING_KEY_UNRESOLVED
    ):
        return False
    return routing_matches(
        consumer.meta.get("routing_key", ""), provider.meta.get("routing_key", "")
    )


def binding_pass(state: MatchState) -> None:
    """Link every consumer of a bound queue, else the binding, to the exchange's publishers."""
    queue_consumers: dict[str, list[Contract]] = defaultdict(list)
    bindings: list[Contract] = []
    for c in state.consumers:
        if c.contract_type != "topic":
            continue
        if is_binding(c):
            bindings.append(c)
        elif c.meta.get("kind") == TOPIC_KIND_QUEUE:
            queue_consumers[str(c.meta.get("topic", "")).lower()].append(c)

    for binding in bindings:
        publishers = [
            p
            for p in state.provider_index.get(normalize_contract_id(binding.contract_id), [])
            if accepts(p, binding)
        ]
        readers = queue_consumers.get(str(binding.meta.get("queue", "")).lower())
        for provider in publishers:
            if not readers:
                if not internal(provider, binding):
                    state.add(
                        binding, provider, "exact", min(provider.confidence, binding.confidence)
                    )
                continue
            for consumer in readers:
                if internal(provider, consumer):
                    continue
                state.add(
                    consumer,
                    provider,
                    "exact",
                    min(provider.confidence, consumer.confidence, binding.confidence),
                    via_alias=True,
                )


__all__ = ["accepts", "binding_pass", "is_binding", "routing_matches"]
