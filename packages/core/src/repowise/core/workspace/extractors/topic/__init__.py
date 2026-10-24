"""Message topic and queue contract extraction.

Each broker is a :class:`.dialect.TopicDialect` table (or a dialect of its own
shape) in its own module, registered in :data:`DIALECTS`. Matching, including
queues reached through an exchange binding and pattern subscriptions, happens
downstream in :mod:`repowise.core.workspace.matching`.
"""

from __future__ import annotations

from ..dialect import ContractDialect, DialectExtractor
from .aws import SNS, SQS
from .bullmq import BULLMQ
from .csharp_events import CSHARP_EVENTS
from .dialect import topic_identity
from .jms import JMS
from .kafka import KAFKA
from .laravel import LARAVEL_QUEUES
from .nats import NATS
from .nestjs import NESTJS
from .rabbitmq import RABBITMQ
from .redis import REDIS

# Order settles which broker names a call two dialects read alike: the first
# contract per file, id and role is kept, so Redis (read only where a Redis
# client is imported) precedes NATS (read on any `client.publish`).
DIALECTS: tuple[ContractDialect, ...] = (
    KAFKA,
    RABBITMQ,
    REDIS,
    NATS,
    BULLMQ,
    SQS,
    SNS,
    NESTJS,
    LARAVEL_QUEUES,
    CSHARP_EVENTS,
    JMS,
)


class TopicExtractor(DialectExtractor):
    """Extract message topic/queue contracts from source files."""

    dialects = DIALECTS
    identity = staticmethod(topic_identity)


__all__ = ["DIALECTS", "TopicExtractor"]
