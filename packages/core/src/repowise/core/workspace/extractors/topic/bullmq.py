"""BullMQ and Bull job queues, directly and through ``@nestjs/bullmq``.

A ``Queue`` adds jobs and a ``Worker`` processes them, both named by the queue.
``Queue`` and ``Worker`` are common class names, so each call is read only in
a file importing one of the libraries.
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import TOPIC_KIND_QUEUE

from ..langs import JS_TS
from ..strings import Arg
from .dialect import TopicCall, TopicDialect

# The import specifiers `bullmq`, `bull`, `@nestjs/bull(mq)` and `bee-queue`.
_BULL = ("bullmq", "'bull'", '"bull"', "/bull", "bee-queue")
_NEST_BULL = ("@nestjs/bull",)
_FIRST = Arg(pos=0)


def _queue(head: str, role: str, label: str, name: Arg, requires: tuple[str, ...]) -> TopicCall:
    return TopicCall(
        head=re.compile(head),
        role=role,
        label=label,
        extensions=JS_TS,
        name=name,
        kind=TOPIC_KIND_QUEUE,
        requires=requires,
    )


BULLMQ = TopicDialect(
    name="bullmq",
    calls=(
        _queue(r"\bnew\s+Queue\s*(?:<[^>()]*>)?\s*\(", "provider", "new Queue", _FIRST, _BULL),
        _queue(r"\bnew\s+Worker\s*(?:<[^>()]*>)?\s*\(", "consumer", "new Worker", _FIRST, _BULL),
        # A flow adds jobs to the queue its `queueName` names.
        _queue(
            r"\.\s*add(?:Bulk)?\s*\(",
            "provider",
            "FlowProducer.add",
            Arg(keys=("queueName",)),
            ("FlowProducer",),
        ),
        _queue(
            r"@Processor\s*\(",
            "consumer",
            "@Processor",
            Arg(keys=("name",), pos=0),
            _NEST_BULL,
        ),
        _queue(r"@InjectQueue\s*\(", "provider", "@InjectQueue", _FIRST, _NEST_BULL),
    ),
)

__all__ = ["BULLMQ"]
