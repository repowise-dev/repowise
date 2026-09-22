"""Kafka producers and consumers: Spring Kafka, kafkajs, kafka-python / confluent, sarama."""

from __future__ import annotations

import re

from ..langs import GO, JAVA, JS_TS, PYTHON
from ..strings import Arg
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
        ),
        TopicCall(
            head=re.compile(r"kafkaTemplate\.send\s*\("),
            role="provider",
            label="kafkaTemplate.send",
            extensions=JAVA,
            name=_FIRST,
        ),
        TopicCall(
            head=re.compile(r"producer\.send\s*\("),
            role="provider",
            label="producer.send({topic})",
            extensions=JS_TS,
            name=_TOPIC,
        ),
        TopicCall(
            head=re.compile(r"consumer\.subscribe\s*\("),
            role="consumer",
            label="consumer.subscribe({topic})",
            extensions=JS_TS,
            name=_TOPIC,
        ),
        TopicCall(
            head=re.compile(r"KafkaConsumer\s*\("),
            role="consumer",
            label="KafkaConsumer",
            extensions=PYTHON,
            name=_FIRST,
            confidence=0.7,
        ),
        TopicCall(
            head=re.compile(r"producer\.produce\s*\("),
            role="provider",
            label="producer.produce",
            extensions=PYTHON | JS_TS,
            name=Arg(keys=("topic",), pos=0),
            confidence=0.7,
        ),
        TopicCall(
            head=re.compile(r"ConsumePartition\s*\("),
            role="consumer",
            label="ConsumePartition",
            extensions=GO,
            name=_FIRST,
            confidence=0.7,
        ),
    ),
)

__all__ = ["KAFKA"]
