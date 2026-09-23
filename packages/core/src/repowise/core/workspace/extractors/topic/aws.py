"""Amazon SQS queues and SNS topics: AWS SDK v3 commands, v2 client calls, sqs-consumer, boto3.

Every call names its destination by URL or ARN (``QueueUrl``, ``TopicArn``),
never by name, so the name is the URL's last path segment or the ARN's last
field: ``https://sqs.eu-west-1.amazonaws.com/123/orders`` and
``arn:aws:sns:eu-west-1:123:orders`` are both ``orders``. A URL built as
``${base}/orders`` still names its queue. A call is read only when its file
spells the key, so ``bot.sendMessage(chatId, text)`` is never a queue.
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import TOPIC_KIND_QUEUE, TOPIC_KIND_TOPIC

from ..langs import JS_TS, PYTHON
from ..strings import Arg
from .dialect import TopicCall, TopicDialect, last_segment

_QUEUE_URL = Arg(keys=("QueueUrl", "queueUrl"))
_TOPIC_ARN = Arg(keys=("TopicArn",))
_SQS = ("QueueUrl", "queueUrl")
_SNS = ("TopicArn",)
_queue_name = last_segment("/")
_topic_name = last_segment(":")


def _sqs(head: str, role: str, label: str, extensions: frozenset[str]) -> TopicCall:
    return TopicCall(
        head=re.compile(head),
        role=role,
        label=label,
        extensions=extensions,
        name=_QUEUE_URL,
        kind=TOPIC_KIND_QUEUE,
        requires=_SQS,
        normalize=_queue_name,
    )


def _sns(head: str, role: str, label: str, extensions: frozenset[str]) -> TopicCall:
    return TopicCall(
        head=re.compile(head),
        role=role,
        label=label,
        extensions=extensions,
        name=_TOPIC_ARN,
        kind=TOPIC_KIND_TOPIC,
        requires=_SNS,
        normalize=_topic_name,
    )


SQS = TopicDialect(
    name="sqs",
    calls=(
        _sqs(r"\bnew\s+SendMessage(?:Batch)?Command\s*\(", "provider", "SendMessageCommand", JS_TS),
        _sqs(r"\bnew\s+ReceiveMessageCommand\s*\(", "consumer", "ReceiveMessageCommand", JS_TS),
        _sqs(r"\.\s*sendMessage(?:Batch)?\s*\(", "provider", "sqs.sendMessage", JS_TS),
        _sqs(r"\.\s*receiveMessage\s*\(", "consumer", "sqs.receiveMessage", JS_TS),
        _sqs(r"\bConsumer\s*\.\s*create\s*\(", "consumer", "Consumer.create", JS_TS),
        _sqs(r"\.\s*send_message(?:_batch)?\s*\(", "provider", "send_message", PYTHON),
        _sqs(r"\.\s*receive_message\s*\(", "consumer", "receive_message", PYTHON),
    ),
)

SNS = TopicDialect(
    name="sns",
    calls=(
        _sns(r"\bnew\s+PublishCommand\s*\(", "provider", "PublishCommand", JS_TS),
        _sns(r"\bnew\s+SubscribeCommand\s*\(", "consumer", "SubscribeCommand", JS_TS),
        _sns(r"\.\s*publish\s*\(", "provider", "sns.publish", JS_TS | PYTHON),
        _sns(r"\.\s*subscribe\s*\(", "consumer", "sns.subscribe", JS_TS | PYTHON),
    ),
)

__all__ = ["SNS", "SQS"]
