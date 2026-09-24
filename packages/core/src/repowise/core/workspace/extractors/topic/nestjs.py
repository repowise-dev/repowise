"""NestJS microservice messages: ``@EventPattern`` / ``@MessagePattern`` and ``ClientProxy``.

A handler is decorated with the pattern it serves; a caller emits an event or
sends a message through a ``ClientProxy``. ``emit`` and ``send`` are far too
common to read on any receiver, so only the names the file types as a
``ClientProxy`` (or one of its transport subclasses) are read, whatever they
are called. A pattern may be a string or an object (``{ cmd: 'sum' }``), which
Nest serializes with sorted keys; an object pattern is named that way here, so
the handler and the caller agree.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import TYPE_CHECKING

from ..calls import call_sites, typed_receivers
from ..langs import JS_TS
from ..strings import Arg, map_entry, select_argument, split_top_level
from .dialect import TopicCall, topic_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext
    from ..calls import FileStrings

_MICROSERVICES = ("@nestjs/microservices",)
_FIRST = Arg(pos=0)

_HANDLERS = (
    TopicCall(
        head=re.compile(r"@EventPattern\s*\("),
        role="consumer",
        label="@EventPattern",
        extensions=JS_TS,
        name=_FIRST,
        requires=_MICROSERVICES,
    ),
    TopicCall(
        head=re.compile(r"@MessagePattern\s*\("),
        role="consumer",
        label="@MessagePattern",
        extensions=JS_TS,
        name=_FIRST,
        requires=_MICROSERVICES,
    ),
)

# `private readonly client: ClientProxy`, `@Inject('X') billing: ClientKafka`.
_PROXY_TYPE_RE = re.compile(r"Client(?:Proxy|Kafka|RMQ|Nats|Redis|Mqtt)")


@lru_cache(maxsize=256)
def _proxy_calls(receivers: tuple[str, ...]) -> tuple[TopicCall, ...]:
    alts = "|".join(re.escape(r) for r in receivers)
    return tuple(
        TopicCall(
            # `send<number>(...)` carries a type argument.
            head=re.compile(
                rf"(?<![\w$])(?:this\s*\.\s*)?(?:{alts})\s*\.\s*{verb}\s*(?:<[^>()]*>)?\s*\("
            ),
            role="provider",
            label=f"ClientProxy.{verb}",
            extensions=JS_TS,
            name=_FIRST,
        )
        for verb in ("emit", "send")
    )


def _pattern(raw: str, strings: FileStrings) -> str | None:
    """A string pattern's text, or an object pattern as Nest serializes it."""
    raw = raw.strip()
    if not raw.startswith("{"):
        values, _ = strings.resolve([raw], _FIRST)
        return values[0] if values else None
    if not raw.endswith("}"):
        return None
    fields: dict[str, str] = {}
    for entry in split_top_level(raw[1:-1], ","):
        member = map_entry(entry)
        if member is None:
            return None
        values, _ = strings.resolve([member[1]], _FIRST)
        if not values:
            return None
        fields[member[0]] = values[0]
    return json.dumps(fields, sort_keys=True, separators=(",", ":")) if fields else None


class NestMicroservicesDialect:
    name = "nestjs"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if not any(r in content for r in _MICROSERVICES):
            return []  # handlers and ClientProxy both come from this package
        calls = list(_HANDLERS)
        names = set(typed_receivers(content, _PROXY_TYPE_RE))
        if names:
            calls.extend(_proxy_calls(tuple(sorted(names))))
        out: list[Contract] = []
        for call, args, m, strings in call_sites(ctx, calls):
            for raw in select_argument(args, call.name):
                name = _pattern(raw, strings)
                if name:
                    out.append(
                        topic_contract(
                            ctx,
                            name=name,
                            broker=self.name,
                            kind=call.kind,
                            role=call.role,
                            label=call.label,
                            confidence=call.confidence,
                            offset=m.start(),
                        )
                    )
        return out


NESTJS = NestMicroservicesDialect()

__all__ = ["NESTJS"]
