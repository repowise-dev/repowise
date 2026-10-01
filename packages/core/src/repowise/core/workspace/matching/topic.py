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

import re
from collections import defaultdict
from fnmatch import fnmatchcase
from functools import lru_cache
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import (
    TOPIC_KIND_BINDING,
    TOPIC_KIND_QUEUE,
    TOPIC_PATTERN,
    TOPIC_PATTERN_GLOB,
    TOPIC_PATTERN_NATS,
    TOPIC_PATTERN_REGEX,
    TOPIC_PREFIX,
    TOPIC_ROUTING_KEY_UNRESOLVED,
    normalize_contract_id,
)

from .common import find_matching_keys, internal

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from .common import MatchState


@lru_cache(maxsize=4096)
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


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


# What a pattern needs besides wildcards to name anything: `>` or `.*` alone
# subscribes to everything, which says nothing about which service it reads.
_WILDCARD_CHARS_RE = re.compile(r"[*>#?.\[\]\\^$+(){}|]")


def pattern_matches(syntax: str, pattern: str, name: str) -> bool:
    """True when a subscription on *pattern* (in *syntax*) receives topic *name*.

    ``nats``: ``*`` is one dot-separated token, a final ``>`` one or more.
    ``glob`` (Redis ``PSUBSCRIBE``): shell wildcards over the whole name.
    ``regex`` (Kafka ``topicPattern``): the whole name.
    """
    if syntax == TOPIC_PATTERN_NATS:
        words = pattern.lower().split(".")
        if words[-1] == ">":
            words[-1:] = ["*", "#"]
        return _words_match(tuple(words), tuple(name.split(".")))
    if syntax == TOPIC_PATTERN_GLOB:
        return fnmatchcase(name, pattern.lower())
    if syntax == TOPIC_PATTERN_REGEX:
        compiled = _compiled(pattern)
        return compiled is not None and compiled.fullmatch(name) is not None
    return False


def matching_keys(consumer: Contract, provider_index: dict[str, list[Contract]]) -> list[str]:
    """The provider keys *consumer* reaches: its own id, and every name its pattern matches."""
    keys = find_matching_keys(consumer.contract_id, provider_index)
    syntax = consumer.meta.get(TOPIC_PATTERN)
    pattern = str(consumer.meta.get("topic", ""))
    if not syntax or not _WILDCARD_CHARS_RE.sub("", pattern):
        return keys
    return keys + [
        k
        for k in provider_index
        if k.startswith(TOPIC_PREFIX)
        and k not in keys
        and pattern_matches(syntax, pattern, k[len(TOPIC_PREFIX) :])
    ]


# Brokers that meet only their own kind: a BullMQ queue is a set of Redis keys
# only BullMQ reads. Laravel and NestJS name a queue or pattern whose transport
# is configured outside the code (RabbitMQ, SQS, Redis, Kafka, NATS), so either
# meets any broker but those. Every other pair must be the same broker: a Kafka
# topic and a RabbitMQ queue that share a name are two things.
_OWN_KIND_ONLY = frozenset({"bullmq"})
_ANY_TRANSPORT = frozenset({"laravel", "nestjs"})


def same_transport(provider: Contract, consumer: Contract) -> bool:
    """Whether the two ends can be one broker's destination."""
    a, b = provider.meta.get("broker"), consumer.meta.get("broker")
    if a == b or not a or not b:
        return True
    if a in _OWN_KIND_ONLY or b in _OWN_KIND_ONLY:
        return False
    return a in _ANY_TRANSPORT or b in _ANY_TRANSPORT


def is_binding(consumer: Contract) -> bool:
    """A binding row is linked by :func:`binding_pass`, never by the exact pass."""
    return consumer.meta.get("kind") == TOPIC_KIND_BINDING


def accepts(provider: Contract, consumer: Contract) -> bool:
    """Whether *consumer* receives what *provider* publishes, on routing grounds.

    A key the source did not settle routes nowhere: reading it as match-all
    would link every binding on the exchange. The two ends must be able to be
    one broker's destination (:func:`same_transport`), and a Laravel job is
    read by a Laravel worker only if it is the worker's own class.
    """
    if provider.meta.get(TOPIC_ROUTING_KEY_UNRESOLVED) or consumer.meta.get(
        TOPIC_ROUTING_KEY_UNRESOLVED
    ):
        return False
    if not same_transport(provider, consumer):
        return False
    if (
        provider.meta.get("broker") == consumer.meta.get("broker") == "laravel"
        and provider.meta.get("job") != consumer.meta.get("job")
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


__all__ = [
    "accepts",
    "binding_pass",
    "is_binding",
    "matching_keys",
    "pattern_matches",
    "routing_matches",
    "same_transport",
]
