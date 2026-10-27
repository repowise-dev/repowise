"""Message topic and queue contract extraction.

Each broker is a :class:`.dialect.TopicDialect` table (or a dialect of its own
shape) in its own module, registered in :data:`DIALECTS`. Matching, including
queues reached through an exchange binding and pattern subscriptions, happens
downstream in :mod:`repowise.core.workspace.matching`.
"""

from __future__ import annotations

from repowise.core.workspace.contracts import TOPIC_NAMES_UNRESOLVED_STAT

from ..dialect import ContractDialect, DialectExtractor
from .aws import SNS, SQS
from .azure_servicebus import AZURE_SERVICE_BUS
from .bullmq import BULLMQ
from .csharp_events import CSHARP_EVENTS
from .dapr import DAPR, DAPR_SUBSCRIPTIONS
from .dialect import topic_identity
from .jms import JMS
from .kafka import KAFKA
from .laravel import LARAVEL_QUEUES
from .nats import NATS
from .nestjs import NESTJS
from .rabbitmq import RABBITMQ
from .redis import REDIS

# Order settles which broker names a call two dialects read alike: the first
# contract per file, id and role is kept, so in a file importing both a Redis
# and a NATS client a `client.publish` is Redis.
DIALECTS: tuple[ContractDialect, ...] = (
    KAFKA,
    RABBITMQ,
    REDIS,
    NATS,
    BULLMQ,
    SQS,
    SNS,
    AZURE_SERVICE_BUS,
    NESTJS,
    DAPR,
    DAPR_SUBSCRIPTIONS,
    LARAVEL_QUEUES,
    CSHARP_EVENTS,
    JMS,
)


class TopicExtractor(DialectExtractor):
    """Extract message topic/queue contracts from source files."""

    dialects = DIALECTS
    identity = staticmethod(topic_identity)
    unresolved_stat = TOPIC_NAMES_UNRESOLVED_STAT


__all__ = ["DIALECTS", "TopicExtractor"]
