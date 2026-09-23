"""RabbitMQ publishers, consumers and queue bindings: Spring AMQP, amqplib, pika.

A publish names an exchange and a routing key, a consume names a queue, and a
binding connects the two; see :mod:`repowise.core.workspace.matching.topic`.
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import (
    TOPIC_KIND_BINDING,
    TOPIC_KIND_EXCHANGE,
    TOPIC_KIND_QUEUE,
)

from ..langs import JAVA, JS_TS, PYTHON
from ..strings import Arg
from .dialect import TopicCall, TopicDialect

RABBITMQ = TopicDialect(
    name="rabbitmq",
    calls=(
        TopicCall(
            head=re.compile(r"@RabbitListener\s*\("),
            role="consumer",
            label="@RabbitListener",
            extensions=JAVA,
            name=Arg(keys=("queues", "queue")),
            kind=TOPIC_KIND_QUEUE,
        ),
        TopicCall(
            head=re.compile(r"rabbitTemplate\.convertAndSend\s*\("),
            role="provider",
            label="rabbitTemplate.convertAndSend",
            extensions=JAVA,
            name=Arg(pos=0),
            kind=TOPIC_KIND_EXCHANGE,
            # `(exchange, routingKey, message)`; in the two-argument form the
            # second is the message, which does not resolve, so it routes nowhere.
            routing_key=Arg(pos=1),
        ),
        TopicCall(
            head=re.compile(r"channel\.consume\s*\("),
            role="consumer",
            label="channel.consume",
            extensions=JS_TS,
            name=Arg(pos=0),
            kind=TOPIC_KIND_QUEUE,
        ),
        TopicCall(
            head=re.compile(r"channel\.publish\s*\("),
            role="provider",
            label="channel.publish",
            extensions=JS_TS,
            name=Arg(pos=0),
            kind=TOPIC_KIND_EXCHANGE,
            routing_key=Arg(pos=1),
            default_exchange=True,
        ),
        TopicCall(
            head=re.compile(r"channel\.sendToQueue\s*\("),
            role="provider",
            label="channel.sendToQueue",
            extensions=JS_TS,
            name=Arg(pos=0),
            kind=TOPIC_KIND_QUEUE,
        ),
        TopicCall(
            head=re.compile(r"channel\.bindQueue\s*\("),
            role="consumer",
            label="channel.bindQueue",
            extensions=JS_TS,
            name=Arg(pos=1),
            kind=TOPIC_KIND_BINDING,
            routing_key=Arg(pos=2),
            queue=Arg(pos=0),
        ),
        TopicCall(
            head=re.compile(r"channel\.basic_consume\s*\("),
            role="consumer",
            label="basic_consume",
            extensions=PYTHON,
            name=Arg(keys=("queue",), pos=0),
            kind=TOPIC_KIND_QUEUE,
            confidence=0.7,
        ),
        TopicCall(
            head=re.compile(r"channel\.basic_publish\s*\("),
            role="provider",
            label="basic_publish",
            extensions=PYTHON,
            name=Arg(keys=("exchange",)),
            kind=TOPIC_KIND_EXCHANGE,
            confidence=0.7,
            routing_key=Arg(keys=("routing_key",)),
            default_exchange=True,
        ),
        TopicCall(
            head=re.compile(r"channel\.queue_bind\s*\("),
            role="consumer",
            label="queue_bind",
            extensions=PYTHON,
            name=Arg(keys=("exchange",), pos=1),
            kind=TOPIC_KIND_BINDING,
            confidence=0.7,
            routing_key=Arg(keys=("routing_key",), pos=2),
            queue=Arg(keys=("queue",), pos=0),
        ),
    ),
)

__all__ = ["RABBITMQ"]
