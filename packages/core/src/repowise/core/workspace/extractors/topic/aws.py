"""Amazon SQS queues, SNS topics, and EventBridge events.

Every SQS and SNS call names its destination by URL or ARN (``QueueUrl``, ``TopicArn``),
never by name, so the name is the URL's last path segment or the ARN's last
field: ``https://sqs.eu-west-1.amazonaws.com/123/orders`` and
``arn:aws:sns:eu-west-1:123:orders`` are both ``orders``. A URL built as
``${base}/orders`` still names its queue. A call is read only when its file
spells the key, so ``bot.sendMessage(chatId, text)`` is never a queue.

EventBridge events are identified by ``<source>/<detail-type>``. Publishers invoke
``PutEvents`` (AWS SDK v3 ``PutEventsCommand``, v2 ``putEvents``, boto3 ``put_events``),
and consumers subscribe via rule patterns in CDK (``eventPattern``) or SAM / CloudFormation
(``EventPattern``, ``Pattern``, ``EventBridgeRule``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import TOPIC_KIND_QUEUE, TOPIC_KIND_TOPIC
from repowise.core.workspace.origins import ContractOrigin

from ..langs import JS_TS, PYTHON
from ..strings import Arg, match_paren
from .dialect import TopicCall, TopicDialect, last_segment, topic_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

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

_EVENTBRIDGE_GATES = ("PutEvents", "put_events", "eventPattern", "EventPattern", "EventBridgeRule")
_YAML_EXT = frozenset({".yaml", ".yml"})
_EVENTBRIDGE_EXT = JS_TS | PYTHON | _YAML_EXT

_PUT_EVENTS_HEAD = re.compile(
    r"(?:\bnew\s+PutEventsCommand|\bput_events|\bputEvents)\s*\("
)
_ENTRY_BLOCK = re.compile(r"\{[^{}]*\}")
_SOURCE_VAL = re.compile(r"""['"]?(?:Source|source)['"]?\s*[:=]\s*(?:\[\s*)?['"]([^'"\]]+)['"]""")
_DETAIL_TYPE_VAL = re.compile(r"""['"]?(?:DetailType|detailType|detail-type)['"]?\s*[:=]\s*(?:\[\s*)?['"]([^'"\]]+)['"]""")

_RULE_PATTERN_HEAD = re.compile(r"(?:eventPattern|EventPattern|EventBridgeRule)\s*[:=]\s*\{")

_YAML_SOURCE_RE = re.compile(r"""(?:Source|source):\s*\n\s*-\s*['"]?([A-Za-z0-9_.-]+)['"]?""")
_YAML_DETAIL_RE = re.compile(r"""(?:DetailType|detailType|detail-type):\s*\n\s*-\s*['"]?([A-Za-z0-9_.-]+)['"]?""")


class EventBridgeTopicDialect:
    """Amazon EventBridge event publishers and rule patterns as topic contracts."""

    name = "eventbridge"
    extensions = _EVENTBRIDGE_EXT
    requires = _EVENTBRIDGE_GATES

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        content = ctx.content

        # 1. Publishers: PutEventsCommand, put_events, putEvents
        if "PutEvents" in content or "put_events" in content:
            for pm in _PUT_EVENTS_HEAD.finditer(content):
                open_idx = pm.end() - 1
                close_idx = match_paren(content, open_idx)
                call_body = (
                    content[open_idx:close_idx]
                    if close_idx != -1
                    else content[open_idx : open_idx + 2000]
                )
                sources = [m.group(1) for m in _SOURCE_VAL.finditer(call_body)]
                details = [m.group(1) for m in _DETAIL_TYPE_VAL.finditer(call_body)]
                for s, d in zip(sources, details, strict=False):
                    event_id = f"{s}/{d}"
                    out.append(
                        topic_contract(
                            ctx,
                            name=event_id,
                            broker="eventbridge",
                            kind=TOPIC_KIND_TOPIC,
                            role="provider",
                            label="eventbridge.PutEvents",
                            origin=ContractOrigin.CLIENT_CALL,
                            offset=pm.start(),
                        )
                    )

        # 2. Consumers: CDK rule patterns or SAM / CloudFormation
        if any(g in content for g in ("eventPattern", "EventPattern", "EventBridgeRule")):
            if ctx.suffix in _YAML_EXT:
                s_m = _YAML_SOURCE_RE.search(content)
                d_m = _YAML_DETAIL_RE.search(content)
                if s_m and d_m:
                    event_id = f"{s_m.group(1)}/{d_m.group(1)}"
                    out.append(
                        topic_contract(
                            ctx,
                            name=event_id,
                            broker="eventbridge",
                            kind=TOPIC_KIND_TOPIC,
                            role="consumer",
                            label="EventBridgeRule",
                            origin=ContractOrigin.ROUTE_REGISTRATION,
                            offset=0,
                        )
                    )
            else:
                for rm in _RULE_PATTERN_HEAD.finditer(content):
                    open_idx = rm.end() - 1
                    close_idx = match_paren(content, open_idx, closer="}")
                    block = (
                        content[open_idx:close_idx]
                        if close_idx != -1
                        else content[open_idx : open_idx + 2000]
                    )
                    s_m = _SOURCE_VAL.search(block)
                    d_m = _DETAIL_TYPE_VAL.search(block)
                    if s_m and d_m:
                        event_id = f"{s_m.group(1)}/{d_m.group(1)}"
                        out.append(
                            topic_contract(
                                ctx,
                                name=event_id,
                                broker="eventbridge",
                                kind=TOPIC_KIND_TOPIC,
                                role="consumer",
                                label="EventBridgeRule",
                                origin=ContractOrigin.ROUTE_REGISTRATION,
                                offset=rm.start(),
                            )
                        )

        return out


EVENTBRIDGE = EventBridgeTopicDialect()

__all__ = ["EVENTBRIDGE", "SNS", "SQS"]
