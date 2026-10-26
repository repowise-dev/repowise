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

from ..langs import CSHARP
from ..strings import Arg
from .dialect import ReceiverCall, TopicCall, TopicDialect, topic_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


_DAPR = ("Dapr",)
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
