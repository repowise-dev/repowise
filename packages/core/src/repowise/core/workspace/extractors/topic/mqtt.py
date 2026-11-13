"""MQTT publishers and subscribers.

Reading mqtt.js publish/subscribe (gated on an mqtt import), paho for Python
and Java, MQTTnet (WithTopic, SubscribeAsync) for C#, and the HiveMQ client.
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import TOPIC_KIND_TOPIC, TOPIC_PATTERN_MQTT

from ..langs import CSHARP, GO, JAVA, JS_TS, PYTHON
from ..strings import Arg
from .dialect import TopicCall, TopicDialect

_RECEIVER = r"(?:client|mqttClient|mqtt_client|mqtt|conn)\s*\.\s*"

_MQTT_JS_CLIENT = ("'mqtt'", '"mqtt"')
_PAHO_PY_CLIENT = ("paho.mqtt", "paho")
_PAHO_JAVA_CLIENT = ("org.eclipse.paho", "eclipse/paho")
_HIVEMQ_CLIENT = ("com.hivemq.client", "hivemq")
_MQTTNET_CLIENT = ("MQTTnet", "mqttnet")

MQTT = TopicDialect(
    name="mqtt",
    calls=(
        # mqtt.js
        TopicCall(
            head=re.compile(_RECEIVER + r"subscribe\s*\("),
            role="consumer",
            label="mqtt.subscribe",
            extensions=JS_TS,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            pattern=TOPIC_PATTERN_MQTT,
            requires=_MQTT_JS_CLIENT,
        ),
        TopicCall(
            head=re.compile(_RECEIVER + r"publish\s*\("),
            role="provider",
            label="mqtt.publish",
            extensions=JS_TS,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            requires=_MQTT_JS_CLIENT,
        ),
        # paho (Python, Go)
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Subscribe|subscribe)\s*\("),
            role="consumer",
            label="mqtt.subscribe",
            extensions=PYTHON | GO,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            pattern=TOPIC_PATTERN_MQTT,
            requires=_PAHO_PY_CLIENT,
        ),
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Publish|publish)\s*\("),
            role="provider",
            label="mqtt.publish",
            extensions=PYTHON | GO,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            requires=_PAHO_PY_CLIENT,
        ),
        # paho (Java)
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Subscribe|subscribe)\s*\("),
            role="consumer",
            label="mqtt.subscribe",
            extensions=JAVA,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            pattern=TOPIC_PATTERN_MQTT,
            requires=_PAHO_JAVA_CLIENT,
        ),
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Publish|publish)\s*\("),
            role="provider",
            label="mqtt.publish",
            extensions=JAVA,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            requires=_PAHO_JAVA_CLIENT,
        ),
        # HiveMQ (Java)
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Subscribe|subscribe)\s*\("),
            role="consumer",
            label="mqtt.subscribe",
            extensions=JAVA,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            pattern=TOPIC_PATTERN_MQTT,
            requires=_HIVEMQ_CLIENT,
        ),
        TopicCall(
            head=re.compile(_RECEIVER + r"(?:Publish|publish)\s*\("),
            role="provider",
            label="mqtt.publish",
            extensions=JAVA,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            requires=_HIVEMQ_CLIENT,
        ),
        TopicCall(
            head=re.compile(r"(?:[\w$]+\s*\.\s*)?topicFilter\s*\("),
            role="consumer",
            label="mqtt.topicFilter",
            extensions=JAVA,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            pattern=TOPIC_PATTERN_MQTT,
            requires=_HIVEMQ_CLIENT,
        ),
        TopicCall(
            head=re.compile(r"(?:[\w$]+\s*\.\s*)?topic\s*\("),
            role="provider",
            label="mqtt.topic",
            extensions=JAVA,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            requires=_HIVEMQ_CLIENT,
        ),
        # MQTTnet (C#)
        TopicCall(
            head=re.compile(r"(?:[\w$]+\s*\.\s*)?SubscribeAsync\s*\("),
            role="consumer",
            label="mqtt.SubscribeAsync",
            extensions=CSHARP,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            pattern=TOPIC_PATTERN_MQTT,
            requires=_MQTTNET_CLIENT,
        ),
        TopicCall(
            head=re.compile(r"(?:[\w$]+\s*\.\s*)?WithTopic\s*\("),
            role="provider",
            label="mqtt.WithTopic",
            extensions=CSHARP,
            name=Arg(pos=0),
            kind=TOPIC_KIND_TOPIC,
            requires=_MQTTNET_CLIENT,
        ),
    ),
)

__all__ = ["MQTT"]
