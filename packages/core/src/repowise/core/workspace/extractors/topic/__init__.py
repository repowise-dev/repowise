"""Message topic and queue contract extraction.

Each broker is a :class:`.dialect.TopicDialect` table in its own module,
registered in :data:`DIALECTS`. Matching, including queues reached through an
exchange binding, happens downstream in :mod:`repowise.core.workspace.matching`.
"""

from __future__ import annotations

from ..dialect import ContractDialect, DialectExtractor
from .dialect import topic_identity
from .kafka import KAFKA
from .nats import NATS
from .rabbitmq import RABBITMQ

DIALECTS: tuple[ContractDialect, ...] = (KAFKA, RABBITMQ, NATS)


class TopicExtractor(DialectExtractor):
    """Extract message topic/queue contracts from source files."""

    dialects = DIALECTS
    identity = staticmethod(topic_identity)


__all__ = ["DIALECTS", "TopicExtractor"]
