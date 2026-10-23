"""Kafka producers and consumers.

Spring Kafka, kafka-clients, kafkajs, kafka-python, aiokafka, confluent-kafka
(Python and .NET), and Go sarama, segmentio/kafka-go and franz-go. A client
whose calls (``send``, ``produce``, ``subscribe``) are too common to read on
any receiver is a :class:`.dialect.ReceiverCall`: read on the names the file
declares with the client's type, whatever they are called. Go clients name
the topic in a struct literal's ``Topic`` field, read as the literal's
arguments.
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import TOPIC_PATTERN_REGEX
from repowise.core.workspace.origins import ContractOrigin

from ..langs import CSHARP, GO, JAVA, JS_TS, PYTHON
from ..strings import Arg, unescape_backslashes
from .dialect import ReceiverCall, TopicCall, TopicDialect

_TOPIC = Arg(keys=("topics", "topic"))
_FIRST = Arg(pos=0)
_TOPIC_OR_FIRST = Arg(keys=("topics", "topic"), pos=0)
_GO_TOPIC = Arg(keys=("Topic",))
_SARAMA = ("sarama",)
_KAFKA_GO = ("segmentio/kafka-go",)
_FRANZ = ("franz-go/pkg/kgo",)
_CONFLUENT_PY = ("confluent_kafka",)
_CONFLUENT_NET = ("Confluent.Kafka",)

KAFKA = TopicDialect(
    name="kafka",
    receivers=(
        ReceiverCall(
            types=re.compile(r"\b(?:KafkaTemplate|KafkaOperations|ReactiveKafkaProducerTemplate)\b"),
            verbs="send",
            role="provider",
            label="KafkaTemplate.send",
            extensions=JAVA,
            name=_FIRST,
            requires=("KafkaTemplate", "KafkaOperations", "ReactiveKafkaProducerTemplate"),
        ),
        ReceiverCall(
            types=re.compile(r"\b(?:confluent_kafka\.)?(?:SerializingProducer|Producer)\b"),
            verbs="produce",
            role="provider",
            label="Producer.produce",
            extensions=PYTHON,
            name=Arg(keys=("topic",), pos=0),
            requires=_CONFLUENT_PY,
        ),
        ReceiverCall(
            types=re.compile(r"\b(?:confluent_kafka\.)?(?:DeserializingConsumer|Consumer)\b"),
            verbs="subscribe",
            role="consumer",
            label="Consumer.subscribe",
            extensions=PYTHON,
            name=_TOPIC_OR_FIRST,
            requires=_CONFLUENT_PY,
        ),
        ReceiverCall(
            types=re.compile(r"\b(?:AIO)?KafkaProducer\b"),
            verbs="send|send_and_wait",
            role="provider",
            label="KafkaProducer.send",
            extensions=PYTHON,
            name=Arg(keys=("topic",), pos=0),
            requires=("KafkaProducer",),
        ),
        ReceiverCall(
            types=re.compile(r"\b(?:AIO)?KafkaConsumer\b"),
            verbs="subscribe",
            role="consumer",
            label="KafkaConsumer.subscribe",
            extensions=PYTHON,
            name=_TOPIC_OR_FIRST,
            requires=("KafkaConsumer",),
        ),
        ReceiverCall(
            types=re.compile(r"\b(?:IProducer|ProducerBuilder)\b"),
            verbs="ProduceAsync|Produce",
            role="provider",
            label="IProducer.Produce",
            extensions=CSHARP,
            name=Arg(keys=("topic",), pos=0),
            requires=_CONFLUENT_NET,
        ),
        ReceiverCall(
            types=re.compile(r"\b(?:IConsumer|ConsumerBuilder)\b"),
            verbs="Subscribe",
            role="consumer",
            label="IConsumer.Subscribe",
            extensions=CSHARP,
            name=Arg(keys=("topics", "topic"), pos=0),
            requires=_CONFLUENT_NET,
        ),
    ),
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
            name=Arg(pos=0, variadic=True),  # `KafkaConsumer(*topics, group_id=...)`
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
        TopicCall(
            head=re.compile(r"new\s+ProducerRecord\s*(?:<[^<>()]*>)?\s*\("),
            role="provider",
            label="ProducerRecord",
            extensions=JAVA,
            name=_FIRST,
            requires=("org.apache.kafka.clients.producer",),
        ),
        TopicCall(
            head=re.compile(r"sarama\.ProducerMessage\s*\{"),
            role="provider",
            label="sarama.ProducerMessage",
            extensions=GO,
            name=_GO_TOPIC,
            requires=_SARAMA,
        ),
        TopicCall(
            # ConsumerGroup.Consume(ctx, []string{"orders"}, handler): the topic
            # list literal is required, as `Consume` alone is any receiver's.
            head=re.compile(r"\.Consume\s*\((?=[^,]*,\s*\[\]string\s*\{)"),
            role="consumer",
            label="ConsumerGroup.Consume",
            extensions=GO,
            name=Arg(pos=1),
            requires=("ConsumerGroup",),
        ),
        TopicCall(
            # Not `kafka.Message{Topic: t}`: a consumer builds one to commit an offset.
            head=re.compile(r"kafka\.(?:Writer|WriterConfig)\s*\{"),
            role="provider",
            label="kafka.Writer",
            extensions=GO,
            name=_GO_TOPIC,
            requires=_KAFKA_GO,
        ),
        TopicCall(
            head=re.compile(r"kafka\.ReaderConfig\s*\{"),
            role="consumer",
            label="kafka.ReaderConfig",
            extensions=GO,
            name=Arg(keys=("Topic", "GroupTopics")),
            requires=_KAFKA_GO,
        ),
        TopicCall(
            head=re.compile(r"kgo\.Record\s*\{"),
            role="provider",
            label="kgo.Record",
            extensions=GO,
            name=_GO_TOPIC,
            requires=_FRANZ,
        ),
        TopicCall(
            head=re.compile(r"kgo\.DefaultProduceTopic\s*\("),
            role="provider",
            label="kgo.DefaultProduceTopic",
            extensions=GO,
            name=_FIRST,
            requires=_FRANZ,
        ),
        TopicCall(
            head=re.compile(r"kgo\.ConsumeTopics\s*\("),
            role="consumer",
            label="kgo.ConsumeTopics",
            extensions=GO,
            name=Arg(pos=0, variadic=True),
            requires=_FRANZ,
        ),
    ),
)

__all__ = ["KAFKA"]
