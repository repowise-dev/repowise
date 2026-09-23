"""socket.io events, NestJS gateways, and ``ws`` / browser websocket endpoints.

A socket.io message is an event in a namespace: whoever calls ``emit`` is its
provider, whoever registers ``on`` its consumer, on the server and the client
alike. ``emit`` and ``on`` are every EventEmitter's too, so events are read
only in a file importing socket.io (or a NestJS gateway), on a receiver whose
name says it is a socket (``socket``, ``io``, ``nsp``, ``server``,
``client``, ...) or that the file binds to one (``const s = io(url)``), and
never for the library's own lifecycle events.

The namespace is the one the file names: ``io.of('/admin')`` on the server,
``io('https://host/admin')`` on the client, ``@WebSocketGateway({ namespace })``
in NestJS, else ``/``. An emit chained on ``.of('/x')`` is in ``/x``. A server
file that uses the default namespace beside a named one, or a file naming
two, leaves its other events unread rather than guess which is which.

``ws`` servers and every ``new WebSocket(url)`` connection are endpoints,
identified by path like any other socket endpoint.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..calls import call_chain, call_sites, file_strings
from ..langs import JS_TS
from ..strings import Arg, call_arguments, match_paren
from .dialect import SocketCall, SocketDialect, event_contract, event_contracts, path_identity

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext
    from ..calls import FileStrings

_SOCKET_IO = ("socket.io", "@nestjs/websockets")
_TRANSPORT = "socket.io"

# Events the library raises itself (emitting one is refused by socket.io), and
# the stream events a `client` of another library emits in the same file.
_RESERVED = frozenset({
    "connect", "connection", "connect_error", "disconnect", "disconnecting", "error",
    "newListener", "removeListener", "reconnect", "reconnect_attempt", "reconnect_error",
    "reconnect_failed", "ping", "pong", "open", "close", "upgrade",
    "end", "data", "finish", "drain", "exit", "readable", "timeout",
})  # fmt: skip

# The role of each event call: whoever emits provides the event.
_VERBS = {
    "emit": "provider",
    "emitWithAck": "provider",
    "serverSideEmit": "provider",
    "serverSideEmitWithAck": "provider",
    "on": "consumer",
    "once": "consumer",
}
# Receiver names that say "socket". `client` and `server` are also any other
# library's (a Redis client, an HTTP server), so they count only in a NestJS
# gateway, where they are the socket and its server, or when the file binds
# them to socket.io itself.
_RECEIVER_RE = re.compile(r"(?i)socket|^io$|^nsp$|namespace")
_GATEWAY_RECEIVERS = frozenset({"client", "server"})
# What sits between a receiver and `.emit(` / `.on(`: `.to(room)`, `.broadcast`, `.of('/x')`.
_MODIFIERS = (
    r"(?:\s*\??\.\s*(?:to|in|except|broadcast|volatile|compress|timeout|local|of)\b"
    r"(?:\s*\([^()]*\))?)*"
)
_OWNER_RE = re.compile(rf"(?P<recv>[A-Za-z_$][\w$]*){_MODIFIERS}\s*$")
_OF_RE = re.compile(r"\.\s*of\s*\(")
_EVENT_RE = re.compile(r"\??\.\s*(?P<verb>" + "|".join(sorted(_VERBS, key=len, reverse=True)) + r")\s*\(")
_FIRST = Arg(pos=0)


def _namespace(text: str) -> str | None:
    """A namespace as written (``admin``, ``/admin``, a client URL) as ``/admin``.

    A URL names the namespace by its path: ``${API_URL}/chat`` is ``/chat``
    and a bare host or base is ``/``. A namespace with a hole is ``None``.
    """
    if "://" in text or text.startswith("${"):
        if "/" not in text.split("://", 1)[-1]:
            return "/"
        path = path_identity(text)
        return None if path is None or "{" in path else path
    if "${" in text:
        return None
    return "/" + text.strip("/")


def _event(text: str) -> str | None:
    return None if text in _RESERVED else text


_CLIENT_CONNECT = SocketCall(
    head=re.compile(r"(?<![\w$.])io(?:\s*\.\s*connect)?\s*\("),
    role="consumer",
    transport=_TRANSPORT,
    label="io",
    extensions=JS_TS,
    name=_FIRST,
    requires=("socket.io-client",),
    normalize=_namespace,
)
_NAMESPACE_CALLS = (
    SocketCall(
        head=re.compile(r"\.\s*of\s*\("),
        role="provider",
        transport=_TRANSPORT,
        label="io.of",
        extensions=JS_TS,
        name=_FIRST,
        requires=("socket.io",),
        normalize=_namespace,
    ),
    _CLIENT_CONNECT,
    SocketCall(
        head=re.compile(r"@WebSocketGateway\s*\("),
        role="provider",
        transport=_TRANSPORT,
        label="@WebSocketGateway",
        extensions=JS_TS,
        name=Arg(keys=("namespace",)),
        requires=("@nestjs/websockets",),
        normalize=_namespace,
    ),
)
_SUBSCRIBE_MESSAGE = SocketCall(
    head=re.compile(r"@SubscribeMessage\s*\("),
    role="consumer",
    transport=_TRANSPORT,
    label="@SubscribeMessage",
    extensions=JS_TS,
    name=_FIRST,
    requires=("@nestjs/websockets",),
    normalize=_event,
)
# `const s = io(url)`, `const admin = io.of('/admin')`, `const io = new Server(http)`.
_BOUND_RE = re.compile(
    r"(?P<var>[A-Za-z_$][\w$]*)\s*(?::[^=;\n]+)?=\s*(?:await\s+)?(?:io|new\s+Server|[\w$.]+\s*\.\s*of)\s*\("
)
# The default namespace used directly on the server: `io.on(...)`, `io.emit(...)`.
_DEFAULT_NAMESPACE_RE = re.compile(r"(?<![\w$.])io\s*\.\s*(?:on|emit|to|in)\s*\(")


class SocketIoDialect:
    name = "socket.io"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if not any(r in content for r in _SOCKET_IO):
            return []
        strings = file_strings(ctx)
        if strings is None:
            return []
        default = self._file_namespace(ctx, strings)
        bound = {m.group("var") for m in _BOUND_RE.finditer(content)}
        if "@nestjs/websockets" in content:
            bound |= _GATEWAY_RECEIVERS
        out: list[Contract] = []
        for m in _EVENT_RE.finditer(content):
            # The receiver ends where the call's `.` begins; `$` holds at *endpos*.
            owner = _OWNER_RE.search(content, max(0, m.start() - 200), m.start())
            recv = owner.group("recv") if owner else ""
            if not recv or not (recv in bound or _RECEIVER_RE.search(recv)):
                continue
            scope = self._chained_namespace(owner.group(0), strings) or default
            close = match_paren(content, m.end() - 1)
            if scope is None or close < 0:
                continue
            calls = [(m.group("verb"), call_arguments(content, m.end() - 1, close) or [], m.start())]
            # `socket.on('a', f).on('b', g)`: every link of the chain has this receiver.
            calls.extend(
                (link.name, link.args, link.offset)
                for link in call_chain(content, close)
                if link.name in _VERBS
            )
            for verb, args, offset in calls:
                events, _ = strings.resolve(args, _FIRST, _event)
                out.extend(
                    event_contract(
                        ctx,
                        scope=scope,
                        event=event,
                        role=_VERBS[verb],
                        transport=_TRANSPORT,
                        label=f"{recv}.{verb}",
                        confidence=0.75,
                        offset=offset,
                    )
                    for event in events
                )
        for call, args, m, _s in call_sites(ctx, (_SUBSCRIBE_MESSAGE,) if default else (), strings):
            events, _ = strings.resolve(args, call.name, call.normalize)
            out.extend(event_contracts(ctx, call, [default], events, m.start()))
        return out

    @staticmethod
    def _file_namespace(ctx: ScanContext, strings: FileStrings) -> str | None:
        """The one namespace the file's unchained events are in, or ``None`` when unsure."""
        namespaces: set[str] = set()
        for call, args, _m, _s in call_sites(ctx, _NAMESPACE_CALLS, strings):
            values, refused = strings.resolve(args, call.name, call.normalize)
            namespaces.update(values)
            # A server namespace this file cannot name makes every event
            # ambiguous. A client URL it cannot read is the configured server
            # address, which names no namespace.
            if refused and call is not _CLIENT_CONNECT:
                return None
        if len(namespaces) == 1 and "/" not in namespaces and _DEFAULT_NAMESPACE_RE.search(ctx.content):
            return None  # the default namespace beside a named one
        if len(namespaces) > 1:
            return None
        return next(iter(namespaces), "/")

    @staticmethod
    def _chained_namespace(owner: str, strings: FileStrings) -> str | None:
        """The namespace of ``io.of('/x').emit(...)``, read from the receiver chain."""
        m = _OF_RE.search(owner)
        if m is None:
            return None
        values, _ = strings.resolve(call_arguments(owner, m.end() - 1) or [], _FIRST, _namespace)
        return values[0] if values else None


_WS = ("'ws'", '"ws"')

WEBSOCKETS = SocketDialect(
    name="websocket",
    calls=(
        SocketCall(
            head=re.compile(r"\bnew\s+(?:WebSocketServer|WebSocket\s*\.\s*Server)\s*\("),
            role="provider",
            transport="ws",
            label="WebSocketServer",
            extensions=JS_TS,
            name=Arg(keys=("path",)),
            requires=_WS,
        ),
        SocketCall(
            head=re.compile(r"@WebSocketGateway\s*\("),
            role="provider",
            transport="websocket",
            label="@WebSocketGateway",
            extensions=JS_TS,
            name=Arg(keys=("path",)),
            requires=("@nestjs/websockets",),
        ),
        SocketCall(
            head=re.compile(r"\bnew\s+WebSocket\s*\("),
            role="consumer",
            transport="websocket",
            label="new WebSocket",
            extensions=JS_TS,
            name=_FIRST,
            confidence=0.75,
            requires=("WebSocket",),
        ),
    ),
)

SOCKET_IO = SocketIoDialect()

__all__ = ["SOCKET_IO", "WEBSOCKETS"]
