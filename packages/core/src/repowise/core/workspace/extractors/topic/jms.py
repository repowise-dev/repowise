"""JMS producers and consumers: Spring `JmsTemplate`, `@JmsListener` and listener containers.

A destination is a queue unless the connection factory says otherwise
(Spring's ``pub-sub-domain`` defaults to false), so every row is a queue.
ActiveMQ and IBM MQ also take a URI-style name: ``queue://orders`` and
``queue:///ORDERS`` are the queue ``orders`` / ``ORDERS``, so the prefix is
dropped. A ``topic://`` name is a different destination from the bare name
and is kept as spelled, so it meets only the same spelling.

A template is read on a receiver whose name says JMS (``jmsTemplate``,
``topicJmsTemplate``, ``getJmsTemplate()``), in a file that names the type.
A template under any other name needs the file's declared receivers (a
:class:`.dialect.ReceiverCall` row, as Kafka's); this dialect declares none
yet, so those calls are missed rather than guessed from the bare ``send`` verb.

``@JmsListener`` names its destination only by the ``destination`` key;
``containerFactory``, ``subscription`` and ``selector`` are not destinations.
A ``${...}`` property placeholder does not resolve inside the file and is
refused.
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import TOPIC_KIND_QUEUE

from ..langs import JAVA, KOTLIN
from ..strings import Arg
from .dialect import TopicCall, TopicDialect

_JVM = JAVA | KOTLIN
_TEMPLATES = ("JmsTemplate", "JmsMessagingTemplate", "JmsOperations")
_FIRST = Arg(pos=0)

# A receiver whose name ends in `jmsTemplate` / `JmsMessagingTemplate` /
# `jmsOperations` (any prefix), or the `getJmsTemplate()` of JmsGatewaySupport.
_RECEIVER = r"(?:\b\w*[jJ]ms(?:Messaging)?(?:Template|Operations)|\bgetJmsTemplate\s*\(\s*\))"

# The one-argument send forms go to the template's default destination, and
# their argument is the payload: `convertAndSend("hello")` names no queue. A
# send is read only when its first argument (a literal or a dotted name) is
# followed by a second.
_FIRST_THEN = r"\s*(?:\"(?:[^\"\\]|\\.)*\"|[\w$.]+)\s*,"
_HAS_SECOND_ARG = rf"(?={_FIRST_THEN})"
# `convertAndSend(payload, m -> ...)` posts the payload to the default
# destination through a post-processor lambda, so a lambda second argument
# refuses the convert forms. A literal payload followed by a named
# post-processor or header map still reads as a destination: the call shape
# alone cannot tell those overloads apart.
_DESTINATION_THEN_PAYLOAD = rf"(?={_FIRST_THEN}(?!\s*(?:[\w$]+|\([^()]*\))\s*->))"


def _queue_name(text: str) -> str | None:
    """``queue://orders`` and IBM MQ's ``queue:///ORDERS?targetClient=1`` name the queue."""
    if not text.startswith("queue://"):
        return text
    return text[len("queue://") :].lstrip("/").partition("?")[0] or None


def _template(verbs: str, role: str, label: str, guard: str = "") -> TopicCall:
    return TopicCall(
        head=re.compile(rf"{_RECEIVER}\s*\.\s*(?:{verbs})\s*\({guard}"),
        role=role,
        label=label,
        extensions=_JVM,
        name=_FIRST,
        kind=TOPIC_KIND_QUEUE,
        requires=_TEMPLATES,
        normalize=_queue_name,
    )


JMS = TopicDialect(
    name="jms",
    calls=(
        TopicCall(
            head=re.compile(r"@JmsListener\s*\("),
            role="consumer",
            label="@JmsListener",
            extensions=_JVM,
            name=Arg(keys=("destination",)),
            kind=TOPIC_KIND_QUEUE,
            requires=("@JmsListener",),
            normalize=_queue_name,
        ),
        # A listener container wired in code (`DefaultMessageListenerContainer`).
        TopicCall(
            head=re.compile(r"\.\s*setDestinationName\s*\("),
            role="consumer",
            label="setDestinationName",
            extensions=_JVM,
            name=_FIRST,
            kind=TOPIC_KIND_QUEUE,
            requires=("MessageListenerContainer",),
            normalize=_queue_name,
        ),
        _template(
            r"convertAndSend|convertSendAndReceive",
            "provider",
            "jmsTemplate.convertAndSend",
            _DESTINATION_THEN_PAYLOAD,
        ),
        # `send(destination, session -> ...)`: the lambda is the message creator.
        _template(r"send|sendAndReceive", "provider", "jmsTemplate.send", _HAS_SECOND_ARG),
        # `receive()` with no argument reads the default destination and is
        # skipped; `receiveSelected("sel")` names a selector, not a queue.
        _template(
            r"receive|receiveAndConvert",
            "consumer",
            "jmsTemplate.receive",
        ),
    ),
)

__all__ = ["JMS"]
