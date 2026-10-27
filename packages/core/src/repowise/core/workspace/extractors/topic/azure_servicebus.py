"""Azure Service Bus queues and topics: the .NET ``Azure.Messaging.ServiceBus`` client and Functions triggers.

A ``ServiceBusClient`` sends with ``CreateSender("orders")`` and receives with
``CreateProcessor``, ``CreateReceiver`` or ``CreateSessionProcessor`` (or their
``CreateSessionReceiver`` / ``AcceptSessionAsync`` forms); an Azure Function
receives with ``[ServiceBusTrigger("orders")]``. The first argument (or a
``queueName`` / ``topicName`` keyword) names the entity.

A receive that also names a subscription (a second argument that is a string
literal or a name mentioning a subscription) reads a topic; any other call
reads a queue. A sender names only the entity, so it is a queue. The broker
matches by name, so a topic's sender and its subscription's processor still
meet. The client calls are read only in a file importing the library; the
trigger names the attribute itself.
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import TOPIC_KIND_QUEUE, TOPIC_KIND_TOPIC
from repowise.core.workspace.origins import ContractOrigin

from ..langs import CSHARP
from ..strings import Arg
from .dialect import TopicCall, TopicDialect

_ENTITY = Arg(keys=("queueName", "topicName"), pos=0)
_CLIENT = ("Azure.Messaging.ServiceBus",)
_TRIGGER = ("ServiceBusTrigger",)

#: A second argument naming a subscription: a string literal, or a name that says so.
_SUBSCRIPTION = (
    r'(?=\s*[^,()]+,\s*(?:(?:"[^"]*"|@"[^"]*"|\w*[Ss]ubscription\w*)\s*[,)]|subscriptionName\s*:))'
)
_NO_SUBSCRIPTION = _SUBSCRIPTION.replace("(?=", "(?!", 1)

_RECEIVERS = r"Create(?:Session)?(?:Processor|Receiver)|AcceptSessionAsync"


def _call(
    head: str,
    role: str,
    label: str,
    kind: str,
    requires: tuple[str, ...],
    origin: ContractOrigin = ContractOrigin.CLIENT_CALL,
) -> TopicCall:
    return TopicCall(
        head=re.compile(head),
        role=role,
        label=label,
        extensions=CSHARP,
        name=_ENTITY,
        kind=kind,
        origin=origin,
        requires=requires,
    )


AZURE_SERVICE_BUS = TopicDialect(
    name="azure_servicebus",
    calls=(
        _call(r"\.\s*CreateSender\s*\(", "provider", "CreateSender", TOPIC_KIND_QUEUE, _CLIENT),
        _call(
            rf"\.\s*(?:{_RECEIVERS})\s*\({_SUBSCRIPTION}",
            "consumer",
            "CreateProcessor",
            TOPIC_KIND_TOPIC,
            _CLIENT,
        ),
        _call(
            rf"\.\s*(?:{_RECEIVERS})\s*\({_NO_SUBSCRIPTION}",
            "consumer",
            "CreateProcessor",
            TOPIC_KIND_QUEUE,
            _CLIENT,
        ),
        _call(
            rf"\bServiceBusTrigger(?:Attribute)?\s*\({_SUBSCRIPTION}",
            "consumer",
            "ServiceBusTrigger",
            TOPIC_KIND_TOPIC,
            _TRIGGER,
            ContractOrigin.ROUTE_REGISTRATION,
        ),
        _call(
            rf"\bServiceBusTrigger(?:Attribute)?\s*\({_NO_SUBSCRIPTION}",
            "consumer",
            "ServiceBusTrigger",
            TOPIC_KIND_QUEUE,
            _TRIGGER,
            ContractOrigin.ROUTE_REGISTRATION,
        ),
    ),
)

__all__ = ["AZURE_SERVICE_BUS"]
