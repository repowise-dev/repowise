"""Dapr pub/sub: a topic named on a component, which runs over any broker.

``[Topic("pubsub", "orders")]`` subscribes a handler and
``daprClient.PublishEventAsync("pubsub", "orders", evt)`` publishes. The first
argument is the pub/sub component, the second the topic, so the topic is read
from position 1. ``PublishEventAsync`` is too common to read on any receiver,
so it is read only on the names the file declares as a ``DaprClient``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import yaml

from repowise.core.workspace.contracts import TOPIC_KIND_TOPIC
from repowise.core.workspace.origins import ContractOrigin

from ..langs import CSHARP, GO, JAVA, JS_TS, PYTHON
from ..strings import Arg
from .dialect import ReceiverCall, TopicCall, TopicDialect, topic_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


_DAPR = ("Dapr",)
_DAPR_GO = ("github.com/dapr/go-sdk",)
_DAPR_JAVA = ("io.dapr",)
_DAPR_JS = ("@dapr/dapr",)
_DAPR_SUBSCRIBE = ("/dapr/subscribe",)
_DAPR_PY = ("dapr.clients", "dapr.ext.grpc")
_TOPIC = Arg(keys=("name", "topicName"), pos=1)

DAPR = TopicDialect(
    name="dapr",
    receivers=(
        ReceiverCall(
            types=re.compile(r"\bDaprClient\b"),
            verbs=r"PublishEventAsync(?:\s*<[^>()]*>)?",
            role="provider",
            label="PublishEventAsync",
            extensions=CSHARP,
            name=_TOPIC,
            requires=_DAPR,
        ),
        ReceiverCall(
            types=re.compile(r"\bDaprClient\b"),
            verbs="publishEvent",
            role="provider",
            label="publishEvent",
            extensions=JAVA,
            name=Arg(pos=1),
            requires=_DAPR_JAVA,
        ),
        ReceiverCall(
            types=re.compile(r"\bDaprClient\b"),
            verbs=r"pubsub\s*\.\s*publish",
            role="provider",
            label="pubsub.publish",
            extensions=JS_TS,
            name=Arg(pos=1),
            requires=_DAPR_JS,
        ),
        ReceiverCall(
            types=re.compile(r"\bDaprServer\b"),
            verbs=r"pubsub\s*\.\s*subscribe",
            role="consumer",
            label="pubsub.subscribe",
            extensions=JS_TS,
            name=Arg(pos=1),
            requires=_DAPR_JS,
        ),
    ),
    calls=(
        TopicCall(
            head=re.compile(r"\[\s*Topic\s*\("),
            role="consumer",
            label="[Topic]",
            extensions=CSHARP,
            name=_TOPIC,
            requires=_DAPR,
        ),
        TopicCall(
            head=re.compile(r"@Topic\s*\("),
            role="consumer",
            label="@Topic",
            extensions=JAVA,
            name=Arg(keys=("name",)),
            requires=_DAPR_JAVA,
        ),
        TopicCall(
            head=re.compile(r"\bpublish_event\s*\("),
            role="provider",
            label="publish_event",
            extensions=PYTHON,
            name=Arg(keys=("topic_name",), pos=1),
            requires=_DAPR_PY,
        ),
        TopicCall(
            head=re.compile(r"@\s*[\w.]+\s*\.\s*subscribe\s*\("),
            role="consumer",
            label="subscribe",
            extensions=PYTHON,
            name=Arg(keys=("topic",)),
            requires=_DAPR_PY,
        ),
        TopicCall(
            head=re.compile(r"\.\s*PublishEvents?\s*\("),
            role="provider",
            label="PublishEvent",
            extensions=GO,
            name=Arg(pos=2),
            requires=_DAPR_GO,
        ),
        TopicCall(
            head=re.compile(r"common\.Subscription\s*\{"),
            role="consumer",
            label="common.Subscription",
            extensions=GO,
            name=Arg(keys=("Topic",)),
            requires=_DAPR_GO,
        ),
        TopicCall(
            head=re.compile(r"""\{(?=[^{}]*['"]?pubsubname['"]?\s*:)""", re.IGNORECASE),
            role="consumer",
            label="/dapr/subscribe",
            extensions=GO | JS_TS | PYTHON,
            name=Arg(keys=("topic", "Topic")),
            requires=_DAPR_SUBSCRIBE,
        ),
    ),
)

_MANIFEST_EXTENSIONS = frozenset({".yaml", ".yml"})


class DaprSubscriptionDialect:
    """A ``kind: Subscription`` manifest names the topic its handler route serves."""

    name = "dapr_subscriptions"
    extensions = _MANIFEST_EXTENSIONS
    requires = ("dapr.io",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        try:
            docs = list(yaml.safe_load_all(ctx.content))
        except (yaml.YAMLError, RecursionError):
            return []
        out: list[Contract] = []
        cursor = 0  # documents come in file order, so each topic is found after the last
        for doc in docs:
            topic = _subscription_topic(doc)
            if not topic:
                continue
            found = re.compile(rf"^\s*topic\s*:\s*['\"]?{re.escape(topic)}", re.M).search(
                ctx.content, cursor
            )
            offset = found.start() if found else cursor
            cursor = found.end() if found else cursor
            out.append(
                topic_contract(
                    ctx,
                    name=topic,
                    broker="dapr",
                    kind=TOPIC_KIND_TOPIC,
                    role="consumer",
                    label="Subscription",
                    origin=ContractOrigin.IDL,
                    offset=offset,
                )
            )
        return out


def _subscription_topic(doc: object) -> str | None:
    """The ``spec.topic`` of a Dapr ``Subscription`` document, or None for any other."""
    if not isinstance(doc, dict):
        return None
    api_version = doc.get("apiVersion")
    if doc.get("kind") != "Subscription" or not (
        isinstance(api_version, str) and api_version.startswith("dapr.io/")
    ):
        return None
    spec = doc.get("spec")
    topic = spec.get("topic") if isinstance(spec, dict) else None
    return topic if isinstance(topic, str) else None


DAPR_SUBSCRIPTIONS = DaprSubscriptionDialect()

__all__ = ["DAPR", "DAPR_SUBSCRIPTIONS"]
