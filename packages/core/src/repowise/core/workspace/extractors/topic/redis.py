"""Redis pub/sub channels and streams.

Pub/sub: ioredis / node-redis, redis-py, Laravel's ``Redis`` facade, go-redis,
Lettuce, Jedis, Spring Data Redis and StackExchange.Redis. Streams: ``XADD`` is a
provider and ``XREAD`` / ``XREADGROUP`` a consumer, with the stream's name as the
topic.

``publish`` and ``subscribe`` are names RxJS, EventEmitters and every other
broker share, so each call is read only in a file importing a Redis client
(``_CLIENT`` and the per-language tokens below); the name must then still
resolve to a string, which an RxJS observer never does. ``psubscribe``
subscribes by glob pattern. BullMQ queues are read by their own dialect.

Reading a name where it is not the first argument:

* go-redis takes a context first, so a channel is argument 1, and a stream is
  the ``Stream`` / ``Streams`` field of the ``redis.XAddArgs`` /
  ``redis.XReadGroupArgs`` literal, as the Kafka dialect reads ``Topic``.
  ``Streams`` lists the names and then one id each (``{"orders", ">"}``); the
  ids are dropped.
* StackExchange.Redis wraps a channel in ``new RedisChannel("x", mode)`` or
  ``RedisChannel.Literal("x")``; the wrapper is part of the head, so the
  channel is argument 0 of the wrapper's own call.
* Jedis ``subscribe(listener, channels...)`` has the listener first.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import TOPIC_KIND_CHANNEL, TOPIC_PATTERN_GLOB
from repowise.core.workspace.origins import ContractOrigin

from ..langs import CSHARP, GO, JAVA, JS_TS, PHP, PYTHON
from ..strings import Arg
from .dialect import TopicCall, TopicDialect

if TYPE_CHECKING:
    from collections.abc import Callable

_FIRST = Arg(pos=0)
_FACADE = ("Redis::",)
# `ioredis`, `redis`, `@redis/client` specifiers; `import redis` / `from redis`.
_CLIENT = ("ioredis", "'redis'", '"redis"', "@redis/", "import redis", "from redis")
# go-redis under its current and its former import path.
_GO_CLIENT = ("github.com/redis/go-redis", "github.com/go-redis/redis")
_LETTUCE = ("io.lettuce",)
_JEDIS = ("redis.clients.jedis",)
_SPRING = ("org.springframework.data.redis",)
_JAVA_CLIENT = (*_LETTUCE, *_JEDIS)
_DOTNET = ("StackExchange.Redis",)

#: A stream is read and written apart from the pub/sub channel of the same name.
_KIND_STREAM = "stream"

# What `XREAD` / `XREADGROUP` list after the stream names, one per stream: `>`
# (undelivered), `$` (new), `+`, `-`, `0`, or a `ms-seq` entry id. Not names.
_STREAM_ID = re.compile(r"[>$+-]|\d+(?:-\d+)?")


def _stream_name(text: str) -> str | None:
    """A stream name, or ``None`` for the id that stands beside it in ``Streams``."""
    return None if _STREAM_ID.fullmatch(text.strip()) else text


def _call(
    head: str,
    role: str,
    label: str,
    extensions: frozenset[str],
    requires: tuple[str, ...] = _CLIENT,
    pattern: str = "",
    *,
    name: Arg = _FIRST,
    kind: str = TOPIC_KIND_CHANNEL,
    normalize: Callable[[str], str | None] | None = None,
    origin: ContractOrigin = ContractOrigin.CLIENT_CALL,
) -> TopicCall:
    return TopicCall(
        head=re.compile(head),
        role=role,
        label=label,
        extensions=extensions,
        name=name,
        kind=kind,
        requires=requires,
        pattern=pattern,
        normalize=normalize,
        origin=origin,
    )


def _stream(
    head: str,
    role: str,
    label: str,
    extensions: frozenset[str],
    requires: tuple[str, ...],
    name: Arg = _FIRST,
    normalize: Callable[[str], str | None] | None = None,
) -> TopicCall:
    return _call(
        head, role, label, extensions, requires, name=name, kind=_KIND_STREAM, normalize=normalize
    )


# --- Go: go-redis ----------------------------------------------------------
# `rdb.Publish(ctx, "orders", p)`, `rdb.Subscribe(ctx, "a", "b")`: the context is first.
_GO_CHANNEL = Arg(pos=1)
_GO_CHANNELS = Arg(pos=1, variadic=True)
_GO_STREAM = Arg(keys=("Stream",))
_GO_STREAMS = Arg(keys=("Streams",))

_GO_CALLS = (
    _call(r"\.\s*Publish\s*\(", "provider", "redis.Publish", GO, _GO_CLIENT, name=_GO_CHANNEL),
    _call(r"\.\s*Subscribe\s*\(", "consumer", "redis.Subscribe", GO, _GO_CLIENT, name=_GO_CHANNELS),
    _call(
        r"\.\s*PSubscribe\s*\(",
        "consumer",
        "redis.PSubscribe",
        GO,
        _GO_CLIENT,
        TOPIC_PATTERN_GLOB,
        name=_GO_CHANNELS,
    ),
    _stream(r"\bredis\.XAddArgs\s*\{", "provider", "redis.XAdd", GO, _GO_CLIENT, _GO_STREAM),
    _stream(
        r"\bredis\.XReadGroupArgs\s*\{",
        "consumer",
        "redis.XReadGroup",
        GO,
        _GO_CLIENT,
        _GO_STREAMS,
        _stream_name,
    ),
    _stream(
        r"\bredis\.XReadArgs\s*\{",
        "consumer",
        "redis.XRead",
        GO,
        _GO_CLIENT,
        _GO_STREAMS,
        _stream_name,
    ),
)

# --- Java: Lettuce, Jedis, Spring Data Redis -------------------------------
_JAVA_CALLS = (
    # Lettuce `commands.publish(ch, msg)` and Jedis `jedis.publish(ch, msg)`.
    _call(r"\.\s*publish\s*\(", "provider", "redis.publish", JAVA, _JAVA_CLIENT),
    # Lettuce `subscribe(String... channels)`.
    _call(
        r"\.\s*subscribe\s*\(",
        "consumer",
        "redis.subscribe",
        JAVA,
        _LETTUCE,
        name=Arg(pos=0, variadic=True),
    ),
    _call(
        r"\.\s*psubscribe\s*\(",
        "consumer",
        "redis.psubscribe",
        JAVA,
        _LETTUCE,
        TOPIC_PATTERN_GLOB,
        name=Arg(pos=0, variadic=True),
    ),
    # Jedis `subscribe(JedisPubSub listener, String... channels)`.
    _call(
        r"\.\s*subscribe\s*\(",
        "consumer",
        "redis.subscribe",
        JAVA,
        _JEDIS,
        name=Arg(pos=1, variadic=True),
    ),
    _call(
        r"\.\s*psubscribe\s*\(",
        "consumer",
        "redis.psubscribe",
        JAVA,
        _JEDIS,
        TOPIC_PATTERN_GLOB,
        name=Arg(pos=1, variadic=True),
    ),
    # Spring `redisTemplate.convertAndSend(channel, payload)`. `convertAndSend` is
    # also RabbitTemplate's and JmsTemplate's, so the receiver must be named for Redis.
    _call(
        r"[rR]edis\w*\s*\.\s*convertAndSend\s*\(",
        "provider",
        "redisTemplate.convertAndSend",
        JAVA,
        _SPRING,
        origin=ContractOrigin.NAME_HEURISTIC,
    ),
    # A listener container wires a topic: `addMessageListener(l, new ChannelTopic("x"))`.
    # A `ChannelTopic` also names the channel a publisher sends on, so it is read
    # only beside a listener container.
    _call(
        r"\bnew\s+ChannelTopic\s*\(|\bChannelTopic\s*\.\s*of\s*\(",
        "consumer",
        "ChannelTopic",
        JAVA,
        ("RedisMessageListenerContainer",),
    ),
    _call(
        r"\bnew\s+PatternTopic\s*\(|\bPatternTopic\s*\.\s*of\s*\(",
        "consumer",
        "PatternTopic",
        JAVA,
        ("RedisMessageListenerContainer",),
        TOPIC_PATTERN_GLOB,
    ),
    # Streams. Lettuce and Jedis `xadd(key, ...)`.
    _stream(r"\.\s*xadd\s*\(", "provider", "redis.xadd", JAVA, _JAVA_CLIENT),
    # Spring `opsForStream().add("orders", map)`; a record argument carries the key itself.
    _stream(
        r"\.\s*opsForStream\s*\([^()]*\)\s*\.\s*add\s*\((?![^,()]*Record)",
        "provider",
        "opsForStream.add",
        JAVA,
        _SPRING,
    ),
    _stream(
        r"\b(?:Map|Object|String)Record\s*\.\s*create\s*\(|\.\s*withStreamKey\s*\(",
        "provider",
        "StreamRecord",
        JAVA,
        _SPRING,
    ),
    # Lettuce and Spring `StreamOffset.lastConsumed("orders")`, read by `xread`,
    # `xreadgroup` and a `StreamMessageListenerContainer`.
    _stream(
        r"\bStreamOffset\s*\.\s*(?:create|from|fromStart|latest|lastConsumed)\s*\(",
        "consumer",
        "StreamOffset",
        JAVA,
        (*_LETTUCE, *_SPRING),
    ),
    # Jedis `xreadGroup(group, consumer, params, Map.entry("orders", StreamEntryID...))`.
    _stream(
        r"\b(?:Map\s*\.\s*entry|singletonMap|SimpleEntry\s*<[^<>]*>)\s*\((?=[^;]*StreamEntryID)",
        "consumer",
        "xreadGroup",
        JAVA,
        _JEDIS,
    ),
)

# --- .NET: StackExchange.Redis ---------------------------------------------
# A channel is a bare string, `new RedisChannel("x", mode)` or `RedisChannel.Literal("x")`
# (`RedisChannel.Pattern("x")` subscribes by glob). The wrapper is part of the head so
# that the channel is the wrapper call's first argument.
_WRAPPED = r"(?:new\s+RedisChannel\s*\(|RedisChannel\s*\.\s*(?:Literal|Pattern)\s*\()"
_NOT_WRAPPED = r"(?!\s*(?:new\s+RedisChannel\b|RedisChannel\s*\.))"


def _dotnet_channel(verbs: str, role: str, label: str, pattern: str = "") -> tuple[TopicCall, ...]:
    return (
        _call(rf"\.\s*(?:{verbs})\s*\({_NOT_WRAPPED}", role, label, CSHARP, _DOTNET, pattern),
        _call(rf"\.\s*(?:{verbs})\s*\(\s*{_WRAPPED}", role, label, CSHARP, _DOTNET, pattern),
    )


_DOTNET_CALLS = (
    *_dotnet_channel("Publish|PublishAsync", "provider", "redis.Publish"),
    # `new RedisChannel("a.*", PatternMode.Pattern)` and `RedisChannel.Pattern` are globs;
    # a name with no wildcard is read as the plain channel it is.
    *_dotnet_channel("Subscribe|SubscribeAsync", "consumer", "redis.Subscribe", TOPIC_PATTERN_GLOB),
    # `db.StreamAdd("orders", "k", "v")`. A `new`-led argument is a `StreamPosition[]`
    # or a `NameValueEntry[]` overload, which names no stream.
    _stream(r"\.\s*StreamAdd(?:Async)?\s*\((?!\s*new\b)", "provider", "StreamAdd", CSHARP, _DOTNET),
    _stream(
        r"\.\s*StreamRead(?:Group)?(?:Async)?\s*\((?!\s*new\b)",
        "consumer",
        "StreamRead",
        CSHARP,
        _DOTNET,
    ),
    # `StreamRead(new[] { new StreamPosition("orders", "0") })`.
    _stream(r"\bnew\s+StreamPosition\s*\(", "consumer", "StreamPosition", CSHARP, _DOTNET),
)

# --- JS / Python / PHP -----------------------------------------------------
_DYNAMIC_CALLS = (
    _call(r"\.\s*publish\s*\(", "provider", "redis.publish", JS_TS | PYTHON),
    _call(r"\.\s*subscribe\s*\(", "consumer", "redis.subscribe", JS_TS | PYTHON),
    _call(
        r"\.\s*p[sS]ubscribe\s*\(",
        "consumer",
        "redis.psubscribe",
        JS_TS | PYTHON,
        pattern=TOPIC_PATTERN_GLOB,
    ),
    _call(r"\bRedis::publish\s*\(", "provider", "Redis::publish", PHP, _FACADE),
    _call(r"\bRedis::subscribe\s*\(", "consumer", "Redis::subscribe", PHP, _FACADE),
    _call(
        r"\bRedis::psubscribe\s*\(",
        "consumer",
        "Redis::psubscribe",
        PHP,
        _FACADE,
        TOPIC_PATTERN_GLOB,
    ),
    # Streams: ioredis `xadd("orders", "*", ...)`, node-redis `xAdd("orders", "*", {...})`,
    # redis-py `xadd("orders", {...})`.
    _stream(r"\.\s*x[aA]dd\s*\(", "provider", "redis.xadd", JS_TS | PYTHON, _CLIENT),
    # node-redis `xReadGroup(group, consumer, { key: "orders", id: ">" })`. ioredis
    # (`"STREAMS", "orders"`) and redis-py (`{"orders": ">"}`) name the stream in a
    # position or a dict key that no argument selector reads, so they are not read.
    _stream(
        r"\.\s*xRead(?:Group)?\s*\(",
        "consumer",
        "redis.xReadGroup",
        JS_TS,
        _CLIENT,
        Arg(keys=("key",)),
    ),
)

REDIS = TopicDialect(
    name="redis",
    calls=(*_DYNAMIC_CALLS, *_GO_CALLS, *_JAVA_CALLS, *_DOTNET_CALLS),
)

__all__ = ["REDIS"]
