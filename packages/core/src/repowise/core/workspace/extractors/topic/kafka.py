"""Kafka producers and consumers: Spring Kafka, kafkajs, kafka-python / confluent, sarama."""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import TOPIC_PATTERN_REGEX
from repowise.core.workspace.origins import ContractOrigin

from ..langs import GO, JAVA, JS_TS, PYTHON
from ..strings import Arg, unescape_backslashes
from .dialect import TopicCall, TopicDialect

_TOPIC = Arg(keys=("topics", "topic"))
_FIRST = Arg(pos=0)

KAFKA = TopicDialect(
    name="kafka",
    calls=(
        TopicCall(
            head=re.compile(r"@KafkaListener\s*\("),
            role="consumer",
            label="@KafkaListener",
            extensions=JAVA,
            name=_TOPIC,
            requires=("@KafkaListener",),
        ),
        TopicCall(
            head=re.compile(r"@KafkaListener\s*\("),
            role="consumer",
            label="@KafkaListener",
            extensions=JAVA,
            name=Arg(keys=("topicPattern",)),
            pattern=TOPIC_PATTERN_REGEX,
            normalize=unescape_backslashes,
            requires=("@KafkaListener",),
        ),
        TopicCall(
            head=re.compile(r"kafkaTemplate\.send\s*\("),
            role="provider",
            label="kafkaTemplate.send",
            extensions=JAVA,
            name=_FIRST,
            requires=("kafkaTemplate",),
        ),
        TopicCall(
            head=re.compile(r"producer\.send\s*\("),
            role="provider",
            label="producer.send({topic})",
            extensions=JS_TS,
            name=_TOPIC,
            requires=("producer",),
        ),
        TopicCall(
            head=re.compile(r"consumer\.subscribe\s*\("),
            role="consumer",
            label="consumer.subscribe({topic})",
            extensions=JS_TS,
            name=_TOPIC,
            requires=("consumer",),
        ),
        TopicCall(
            head=re.compile(r"KafkaConsumer\s*\("),
            role="consumer",
            label="KafkaConsumer",
            extensions=PYTHON,
            name=_FIRST,
            requires=("KafkaConsumer",),
        ),
        TopicCall(
            head=re.compile(r"producer\.produce\s*\("),
            role="provider",
            label="producer.produce",
            extensions=PYTHON | JS_TS,
            name=Arg(keys=("topic",), pos=0),
            origin=ContractOrigin.NAME_HEURISTIC,  # read off a variable named `producer`
            requires=("producer",),
        ),
        TopicCall(
            head=re.compile(r"ConsumePartition\s*\("),
            role="consumer",
            label="ConsumePartition",
            extensions=GO,
            name=_FIRST,
            requires=("ConsumePartition",),
        ),
    ),
)

__all__ = ["KAFKA"]
