"""Redis pub/sub channels: ioredis / node-redis, redis-py, Laravel's ``Redis`` facade.

``publish`` and ``subscribe`` are names RxJS, EventEmitters and every other
broker share, so the JS and Python calls are read only in a file importing a
Redis client; the name must then still resolve to a string, which an RxJS
observer never does. ``psubscribe`` subscribes by glob pattern.
"""

from __future__ import annotations

import re

from repowise.core.workspace.contracts import TOPIC_KIND_CHANNEL, TOPIC_PATTERN_GLOB

from ..langs import JS_TS, PHP, PYTHON
from ..strings import Arg
from .dialect import TopicCall, TopicDialect

_FIRST = Arg(pos=0)
_FACADE = ("Redis::",)
# `ioredis`, `redis`, `@redis/client` specifiers; `import redis` / `from redis`.
_CLIENT = ("ioredis", "'redis'", '"redis"', "@redis/", "import redis", "from redis")


def _call(
    head: str,
    role: str,
    label: str,
    extensions: frozenset[str],
    requires: tuple[str, ...] = _CLIENT,
    pattern: str = "",
) -> TopicCall:
    return TopicCall(
        head=re.compile(head),
        role=role,
        label=label,
        extensions=extensions,
        name=_FIRST,
        kind=TOPIC_KIND_CHANNEL,
        requires=requires,
        pattern=pattern,
    )


REDIS = TopicDialect(
    name="redis",
    calls=(
        _call(r"\.\s*publish\s*\(", "provider", "redis.publish", JS_TS | PYTHON),
        _call(r"\.\s*subscribe\s*\(", "consumer", "redis.subscribe", JS_TS | PYTHON),
        _call(
            r"\.\s*p[sS]ubscribe\s*\(", "consumer", "redis.psubscribe", JS_TS | PYTHON,
            pattern=TOPIC_PATTERN_GLOB,
        ),
        _call(r"\bRedis::publish\s*\(", "provider", "Redis::publish", PHP, _FACADE),
        _call(r"\bRedis::subscribe\s*\(", "consumer", "Redis::subscribe", PHP, _FACADE),
        _call(r"\bRedis::psubscribe\s*\(", "consumer", "Redis::psubscribe", PHP, _FACADE, TOPIC_PATTERN_GLOB),
    ),
)

__all__ = ["REDIS"]
