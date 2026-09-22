"""RabbitMQ publishers, consumers and queue bindings: Spring AMQP, amqplib, pika.

A publish names an exchange and a routing key, a consume names a queue, and a
binding connects the two; see :mod:`repowise.core.workspace.matching.topic`.
"""

from __future__ import annotations

import re

from ..langs import JAVA, JS_TS, PYTHON
from .dialect import KIND_BINDING, KIND_EXCHANGE, KIND_QUEUE, Arg, TopicCall, TopicDialect

RABBITMQ = TopicDialect(
    name="rabbitmq",
    broker="rabbitmq",
    gate=re.compile(r"channel\.|[Rr]abbit"),
    calls=(
        TopicCall(
            head=re.compile(r"@RabbitListener\s*\("),
            role="consumer",
            label="@RabbitListener",
            extensions=JAVA,
            name=Arg(keys=("queues", "queue")),
            kind=KIND_QUEUE,
        ),
        TopicCall(
            head=re.compile(r"rabbitTemplate\.convertAndSend\s*\("),
            role="provider",
            label="rabbitTemplate.convertAndSend",
            extensions=JAVA,
            name=Arg(pos=0),
            kind=KIND_EXCHANGE,
        ),
        TopicCall(
            head=re.compile(r"channel\.consume\s*\("),
            role="consumer",
            label="channel.consume",
            extensions=JS_TS,
            name=Arg(pos=0),
            kind=KIND_QUEUE,
        ),
        TopicCall(
            head=re.compile(r"channel\.publish\s*\("),
            role="provider",
            label="channel.publish",
            extensions=JS_TS,
            name=Arg(pos=0),
            kind=KIND_EXCHANGE,
            routing_key=Arg(pos=1),
            default_exchange=True,
        ),
        TopicCall(
            head=re.compile(r"channel\.sendToQueue\s*\("),
            role="provider",
            label="channel.sendToQueue",
            extensions=JS_TS,
            name=Arg(pos=0),
            kind=KIND_QUEUE,
        ),
        TopicCall(
            head=re.compile(r"channel\.bindQueue\s*\("),
            role="consumer",
            label="channel.bindQueue",
            extensions=JS_TS,
            name=Arg(pos=1),
            kind=KIND_BINDING,
            routing_key=Arg(pos=2),
            queue=Arg(pos=0),
        ),
        TopicCall(
            head=re.compile(r"channel\.basic_consume\s*\("),
            role="consumer",
            label="basic_consume",
            extensions=PYTHON,
            name=Arg(keys=("queue",)),
            kind=KIND_QUEUE,
            confidence=0.7,
        ),
        TopicCall(
            head=re.compile(r"channel\.basic_publish\s*\("),
            role="provider",
            label="basic_publish",
            extensions=PYTHON,
            name=Arg(keys=("exchange",)),
            kind=KIND_EXCHANGE,
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
            kind=KIND_BINDING,
            confidence=0.7,
            routing_key=Arg(keys=("routing_key",), pos=2),
            queue=Arg(keys=("queue",), pos=0),
        ),
    ),
)

__all__ = ["RABBITMQ"]
