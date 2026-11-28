"""Temporal task queues, read as worker and client contracts.

The cross-service contract in Temporal is the task queue name: a worker polls
a queue and a client starts a workflow on it. A worker is the consumer and a
client, child workflow or activity call naming a queue is the provider.

TypeScript (``@temporalio/*``), Go (``go.temporal.io``), Java (``io.temporal``),
Python (``temporalio``) and .NET (``Temporalio``). ``Worker`` and ``taskQueue``
are common names, so every row is read only in a file importing the SDK.
A queue is not a message topic on any broker, so it meets only its own kind
(``_OWN_KIND_ONLY`` in :mod:`..matching.topic`).
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import TOPIC_KIND_QUEUE

from ..langs import CSHARP, GO, JAVA, JS_TS, PYTHON
from ..strings import Arg
from .dialect import TopicCall, TopicDialect

_TS = ("@temporalio/",)
_GO = ("go.temporal.io",)
_JAVA = ("io.temporal",)
_PY = ("temporalio",)
_NET = ("Temporalio",)

_FIRST = Arg(pos=0)
_TS_QUEUE = Arg(keys=("taskQueue",))
_PY_QUEUE = Arg(keys=("task_queue",))
_GO_QUEUE = Arg(keys=("TaskQueue",))


def _queue(
    head: str,
    role: str,
    label: str,
    extensions: frozenset[str],
    name: Arg,
    requires: tuple[str, ...],
) -> TopicCall:
    return TopicCall(
        head=re.compile(head),
        role=role,
        label=label,
        extensions=extensions,
        name=name,
        kind=TOPIC_KIND_QUEUE,
        requires=requires,
    )


TEMPORAL = TopicDialect(
    name="temporal",
    calls=(
        # --- workers: the consumer of a queue ---------------------------------
        _queue(r"\bWorker\s*\.\s*create\s*\(", "consumer", "Worker.create", JS_TS, _TS_QUEUE, _TS),
        _queue(r"\bworker\s*\.\s*New\s*\(", "consumer", "worker.New", GO, Arg(pos=1), _GO),
        _queue(r"\.\s*newWorker\s*\(", "consumer", "newWorker", JAVA, _FIRST, _JAVA),
        # `Worker(client, task_queue="q")`; not `MyWorker(` and not `Worker.create(`.
        _queue(r"(?<![\w$])Worker\s*\(", "consumer", "Worker", PYTHON, _PY_QUEUE, _PY),
        _queue(
            r"\bnew\s+TemporalWorkerOptions\s*\(",
            "consumer",
            "TemporalWorkerOptions",
            CSHARP,
            Arg(keys=("TaskQueue", "taskQueue"), pos=0),
            _NET,
        ),
        _queue(
            r"\bnew\s+TemporalWorkerOptions\s*\{",
            "consumer",
            "TemporalWorkerOptions",
            CSHARP,
            Arg(keys=("TaskQueue",)),
            _NET,
        ),
        # --- clients: the provider of a queue ---------------------------------
        _queue(
            r"\.\s*workflow\s*\.\s*(?:start|execute|signalWithStart)\s*\(",
            "provider",
            "workflow.start",
            JS_TS,
            _TS_QUEUE,
            _TS,
        ),
        _queue(
            r"\b(?:startChild|executeChild)\s*\(",
            "provider",
            "startChild",
            JS_TS,
            _TS_QUEUE,
            _TS,
        ),
        _queue(
            r"\b\w+\.(?:StartWorkflowOptions|ChildWorkflowOptions)\s*\{",
            "provider",
            "StartWorkflowOptions",
            GO,
            _GO_QUEUE,
            _GO,
        ),
        _queue(r"\.\s*setTaskQueue\s*\(", "provider", "setTaskQueue", JAVA, _FIRST, _JAVA),
        _queue(
            r"\.\s*(?:start_workflow|execute_workflow|start_child_workflow|execute_child_workflow)\s*\(",
            "provider",
            "start_workflow",
            PYTHON,
            _PY_QUEUE,
            _PY,
        ),
        _queue(
            r"\bnew\s+WorkflowOptions\s*\(",
            "provider",
            "WorkflowOptions",
            CSHARP,
            Arg(keys=("taskQueue", "TaskQueue"), pos=1),
            _NET,
        ),
        _queue(
            r"\bnew\s+WorkflowOptions\s*\{",
            "provider",
            "WorkflowOptions",
            CSHARP,
            Arg(keys=("TaskQueue",)),
            _NET,
        ),
    ),
)

__all__ = ["TEMPORAL"]
