"""Celery task queues: task decorators, send_task, .delay() and .apply_async().

A task is a queue consumer. It is named either explicitly by the decorator
(``@app.task(name="orders.charge")``, ``@shared_task(name=...)``) or by convention
(``<module>.<function>``). Explicit names have :attr:`.ContractOrigin.ROUTE_REGISTRATION`;
unnamed decorators take their module and function name under
:attr:`.ContractOrigin.NAME_CONVENTION`.

A task is dispatched through ``send_task("name", ...)`` or by invoking ``.delay(...)``
/ ``.apply_async(...)`` on an imported task. Both are providers under
:attr:`.ContractOrigin.CLIENT_CALL`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import TOPIC_KIND_QUEUE
from repowise.core.workspace.origins import ContractOrigin

from ..calls import call_sites
from ..langs import PYTHON
from ..strings import Arg
from .dialect import TopicCall, topic_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_BROKER = "celery"
_CELERY_GATES = ("celery", "shared_task", "send_task", ".delay(", "apply_async")

_TASK_DECORATOR_RE = re.compile(
    r"^[ \t]*@(?:(?:[\w$]+\.)?(?:task|shared_task))\s*(?:\((?P<args>[^()]*)\))?\s*\n"
    r"(?:[ \t]*@[\w.]+(?:\([^()]*\))?\s*\n)*"
    r"[ \t]*(?:async\s+)?def\s+(?P<func>[A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)

_NAME_ARG_RE = re.compile(r"""\bname\s*=\s*['"]([^'"]+)['"]""")

_SEND_TASK_CALL = TopicCall(
    head=re.compile(r"(?:\b[\w$]+\.)?send_task\s*\("),
    role="provider",
    label="celery.send_task",
    extensions=PYTHON,
    name=Arg(pos=0),
    kind=TOPIC_KIND_QUEUE,
    requires=("send_task",),
)

_DISPATCH_RE = re.compile(
    r"\b(?P<receiver>(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*)\s*\.\s*(?P<verb>delay|apply_async)\s*\("
)

_EXCLUDED_RECEIVERS = frozenset({"self", "cls", "super"})


class CeleryTopicDialect:
    """Celery tasks and dispatches as queue topic contracts."""

    name = _BROKER
    extensions = PYTHON
    requires = _CELERY_GATES

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        content = ctx.content

        # 1. Consumers: task definitions
        if "shared_task" in content or "task" in content:
            for m in _TASK_DECORATOR_RE.finditer(content):
                args_str = m.group("args") or ""
                func_name = m.group("func")
                name_m = _NAME_ARG_RE.search(args_str)
                if name_m:
                    task_name = name_m.group(1)
                    origin = ContractOrigin.ROUTE_REGISTRATION
                else:
                    mod_name = Path(ctx.rel_path).stem
                    task_name = f"{mod_name}.{func_name}"
                    origin = ContractOrigin.NAME_CONVENTION

                out.append(
                    topic_contract(
                        ctx,
                        name=task_name,
                        broker=_BROKER,
                        kind=TOPIC_KIND_QUEUE,
                        role="consumer",
                        label="@shared_task",
                        origin=origin,
                        offset=m.start(),
                    )
                )

        # 2. Providers: send_task
        if "send_task" in content:
            for call, args, cm, strings in call_sites(ctx, [_SEND_TASK_CALL]):
                names, _ = strings.resolve(args, call.name)
                for name in names:
                    if name:
                        out.append(
                            topic_contract(
                                ctx,
                                name=name,
                                broker=_BROKER,
                                kind=TOPIC_KIND_QUEUE,
                                role="provider",
                                label="celery.send_task",
                                origin=ContractOrigin.CLIENT_CALL,
                                offset=cm.start(),
                            )
                        )

        # 3. Providers: .delay() and .apply_async()
        if ".delay(" in content or "apply_async" in content:
            for m in _DISPATCH_RE.finditer(content):
                receiver = m.group("receiver")
                if receiver in _EXCLUDED_RECEIVERS:
                    continue
                verb = m.group("verb")
                out.append(
                    topic_contract(
                        ctx,
                        name=receiver,
                        broker=_BROKER,
                        kind=TOPIC_KIND_QUEUE,
                        role="provider",
                        label=f"celery.{verb}",
                        origin=ContractOrigin.CLIENT_CALL,
                        offset=m.start(),
                    )
                )

        return out


CELERY = CeleryTopicDialect()

__all__ = ["CELERY"]
