"""RabbitMQ publishers, consumers and queue bindings: Spring AMQP, amqplib, pika, php-amqplib, Go amqp.

A publish names an exchange and a routing key, a consume names a queue, and a
binding connects the two; see :mod:`repowise.core.workspace.matching.topic`.

amqplib's ``publish`` and ``consume`` are names half the event libraries
share, so they are read only in a file that imports amqplib, whatever the
channel variable is called. ``sendToQueue`` and ``bindQueue`` are amqplib's
alone and are read anywhere, including on a channel handed in from another
module.

Go (streadway/amqp, rabbitmq/amqp091-go) names a declared queue through the
declaration's result (``q.Name``), read back from ``QueueDeclare``
(:class:`.declared.QueueDeclaration`), as are amqplib's ``assertQueue`` and
pika's ``queue_declare`` results.
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import (
    TOPIC_KIND_BINDING,
    TOPIC_KIND_EXCHANGE,
    TOPIC_KIND_QUEUE,
)
from repowise.core.workspace.origins import ContractOrigin

from ..langs import GO, JAVA, JS_TS, PHP, PYTHON
from ..strings import Arg
from .declared import QueueDeclaration
from .dialect import TopicCall, TopicDialect

_AMQPLIB = ("amqplib", "amqp-connection-manager")
_AMQPLIB_QUEUE = QueueDeclaration(call="assertQueue", member="queue")
_PIKA_QUEUE = QueueDeclaration(call="queue_declare", member="method.queue")
_PHP_AMQPLIB = ("PhpAmqpLib",)
_GO_AMQP = ("streadway/amqp", "rabbitmq/amqp091-go")
_GO_QUEUE = QueueDeclaration(call="QueueDeclare", member="Name")

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
            requires=("@RabbitListener",),
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
            requires=("rabbitTemplate",),
        ),
        TopicCall(
            head=re.compile(r"\.\s*consume\s*\("),
            role="consumer",
            label="channel.consume",
            extensions=JS_TS,
            name=Arg(pos=0),
            kind=TOPIC_KIND_QUEUE,
            declared=_AMQPLIB_QUEUE,
            requires=_AMQPLIB,
        ),
        TopicCall(
            head=re.compile(r"\.\s*publish\s*\("),
            role="provider",
            label="channel.publish",
            extensions=JS_TS,
            name=Arg(pos=0),
            kind=TOPIC_KIND_EXCHANGE,
            routing_key=Arg(pos=1),
            default_exchange=True,
            requires=_AMQPLIB,
        ),
        TopicCall(
            head=re.compile(r"\.\s*sendToQueue\s*\("),
            role="provider",
            label="channel.sendToQueue",
            extensions=JS_TS,
            name=Arg(pos=0),
            kind=TOPIC_KIND_QUEUE,
            requires=("sendToQueue",),
        ),
        TopicCall(
            head=re.compile(r"\.\s*bindQueue\s*\("),
            role="consumer",
            label="channel.bindQueue",
            extensions=JS_TS,
            name=Arg(pos=1),
            kind=TOPIC_KIND_BINDING,
            routing_key=Arg(pos=2),
            queue=Arg(pos=0),
            declared=_AMQPLIB_QUEUE,
            requires=("bindQueue",),
        ),
        TopicCall(
            head=re.compile(r"channel\.basic_consume\s*\("),
            role="consumer",
            label="basic_consume",
            extensions=PYTHON,
            name=Arg(keys=("queue",), pos=0),
            kind=TOPIC_KIND_QUEUE,
            declared=_PIKA_QUEUE,
            origin=ContractOrigin.NAME_HEURISTIC,  # a variable named `channel`
            requires=("basic_consume",),
        ),
        TopicCall(
            head=re.compile(r"channel\.basic_publish\s*\("),
            role="provider",
            label="basic_publish",
            extensions=PYTHON,
            name=Arg(keys=("exchange",)),
            kind=TOPIC_KIND_EXCHANGE,
            origin=ContractOrigin.NAME_HEURISTIC,  # a variable named `channel`
            routing_key=Arg(keys=("routing_key",)),
            default_exchange=True,
            requires=("basic_publish",),
        ),
        TopicCall(
            head=re.compile(r"channel\.queue_bind\s*\("),
            role="consumer",
            label="queue_bind",
            extensions=PYTHON,
            name=Arg(keys=("exchange",), pos=1),
            kind=TOPIC_KIND_BINDING,
            origin=ContractOrigin.NAME_HEURISTIC,  # a variable named `channel`
            routing_key=Arg(keys=("routing_key",), pos=2),
            queue=Arg(keys=("queue",), pos=0),
            declared=_PIKA_QUEUE,
            requires=("queue_bind",),
        ),
        # php-amqplib: `basic_publish($msg, $exchange = '', $routing_key = '')`.
        TopicCall(
            head=re.compile(r"->\s*basic_consume\s*\("),
            role="consumer",
            label="basic_consume",
            extensions=PHP,
            name=Arg(keys=("queue",), pos=0),
            kind=TOPIC_KIND_QUEUE,
            requires=_PHP_AMQPLIB,
        ),
        TopicCall(
            head=re.compile(r"->\s*basic_publish\s*\("),
            role="provider",
            label="basic_publish",
            extensions=PHP,
            name=Arg(keys=("exchange",), pos=1),
            kind=TOPIC_KIND_EXCHANGE,
            routing_key=Arg(keys=("routing_key",), pos=2),
            default_exchange=True,
            requires=_PHP_AMQPLIB,
        ),
        TopicCall(
            head=re.compile(r"->\s*queue_bind\s*\("),
            role="consumer",
            label="queue_bind",
            extensions=PHP,
            name=Arg(keys=("exchange",), pos=1),
            kind=TOPIC_KIND_BINDING,
            routing_key=Arg(keys=("routing_key",), pos=2),
            queue=Arg(keys=("queue",), pos=0),
            requires=_PHP_AMQPLIB,
        ),
        # Go: `Publish(exchange, key, mandatory, immediate, msg)`, the context
        # first in `PublishWithContext` and `ConsumeWithContext`; `QueueBind(queue, key, exchange, ...)`.
        TopicCall(
            head=re.compile(r"\.\s*Consume\s*\("),
            role="consumer",
            label="Channel.Consume",
            extensions=GO,
            name=Arg(pos=0),
            kind=TOPIC_KIND_QUEUE,
            declared=_GO_QUEUE,
            requires=_GO_AMQP,
        ),
        TopicCall(
            head=re.compile(r"\.\s*ConsumeWithContext\s*\("),
            role="consumer",
            label="Channel.ConsumeWithContext",
            extensions=GO,
            name=Arg(pos=1),
            kind=TOPIC_KIND_QUEUE,
            declared=_GO_QUEUE,
            requires=_GO_AMQP,
        ),
        TopicCall(
            head=re.compile(r"\.\s*Publish\s*\("),
            role="provider",
            label="Channel.Publish",
            extensions=GO,
            name=Arg(pos=0),
            kind=TOPIC_KIND_EXCHANGE,
            routing_key=Arg(pos=1),
            default_exchange=True,
            requires=_GO_AMQP,
        ),
        TopicCall(
            head=re.compile(r"\.\s*Publish(?:WithContext|WithDeferredConfirmWithContext)\s*\("),
            role="provider",
            label="Channel.PublishWithContext",
            extensions=GO,
            name=Arg(pos=1),
            kind=TOPIC_KIND_EXCHANGE,
            routing_key=Arg(pos=2),
            default_exchange=True,
            requires=_GO_AMQP,
        ),
        TopicCall(
            head=re.compile(r"\.\s*QueueBind\s*\("),
            role="consumer",
            label="Channel.QueueBind",
            extensions=GO,
            name=Arg(pos=2),
            kind=TOPIC_KIND_BINDING,
            routing_key=Arg(pos=1),
            queue=Arg(pos=0),
            declared=_GO_QUEUE,
            requires=_GO_AMQP,
        ),
    ),
)

__all__ = ["RABBITMQ"]
