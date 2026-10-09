"""Broadcast channels over the Pusher protocol: Laravel events, Echo, Reverb, Pusher.

A Laravel event with a ``broadcastOn()`` method is broadcast on the channels
it returns, under the name its ``broadcastAs()`` returns or else its fully
qualified class name. Laravel Echo (and its ``useEcho`` hooks) listens on a
channel for an event, prefixing a bare event name with the ``App\\Events``
namespace exactly as Echo's own formatter does, so ``.listen('OrderShipped')``
meets ``App\\Events\\OrderShipped`` and ``.listen('.order.shipped')`` meets
``broadcastAs() { return 'order.shipped'; }``. Private and presence channels
travel as ``private-`` / ``presence-`` names, which is how raw Pusher clients
and ``$pusher->trigger`` spell them. Reverb speaks the same protocol.

Each contract is ``socket::<channel>#<event>``, the channel's interpolated
parts collapsed to ``{param}``. A notification's ``broadcastOn`` is not read:
Laravel sends every notification as one framework event, which Echo receives
through ``.notification()``, not ``.listen()``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.languages.php_same_namespace import (
    PHP_CLASS_DECL_RE,
    file_namespace,
    qualify_php_name,
)

from ..calls import call_chain, call_sites, file_strings
from ..langs import JS_TS, PHP
from ..strings import Arg, call_arguments, match_paren, unescape_backslashes
from .dialect import SocketCall, channel_scope, event_contracts

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext
    from ..calls import FileStrings

_TRANSPORT = "pusher"
_ECHO_NAMESPACE = "App\\Events"
_FIRST = Arg(pos=0)
_SECOND = Arg(pos=1)

# Channel classes and Echo methods, with the prefix each puts on the wire.
_PHP_CHANNEL_PREFIX = {
    "Channel": "",
    "PrivateChannel": "private-",
    "PresenceChannel": "presence-",
    "EncryptedPrivateChannel": "private-encrypted-",
}
_ANONYMOUS_PREFIX = {"on": "", "private": "private-", "presence": "presence-"}
_ECHO_PREFIX = {
    "channel": "",
    "private": "private-",
    "join": "presence-",
    "encryptedPrivate": "private-encrypted-",
    "useEcho": "private-",
    "useEchoPublic": "",
    "useEchoPresence": "presence-",
}

_BROADCAST_ON_RE = re.compile(r"function\s+broadcastOn\s*\([^)]*\)[^{;]*\{")
_BROADCAST_AS_RE = re.compile(r"function\s+broadcastAs\s*\([^)]*\)[^{;]*\{\s*return\s+(?P<value>[^;]+);")
_NEW_CHANNEL_RE = re.compile(
    r"\bnew\s+\\?(?:[\w\\]*\\)?(?P<cls>" + "|".join(_PHP_CHANNEL_PREFIX) + r")\s*\("
)
# `Echo.private(`, `window.Echo.join(`; the boundary is checked behind the
# literal so the regex keeps a literal-led start.
_ECHO_RE = re.compile(
    r"[Ee]cho(?<![\w$][Ee]cho)\s*\.\s*(?P<kind>private|channel|join|encryptedPrivate)\s*\("
)
# `const channel = pusher.subscribe(`, `this.channel = this.pusher.subscribe(`.
_ASSIGNED_RE = re.compile(r"(?P<var>[A-Za-z_$][\w$.]*)\s*=\s*(?:await\s+)?[\w$.]*$")

_BROADCAST_EVENT = SocketCall(
    head=_BROADCAST_ON_RE,
    role="provider",
    transport=_TRANSPORT,
    label="broadcastOn",
    extensions=PHP,
    name=_FIRST,
)
_PUSHER_TRIGGER = SocketCall(
    head=re.compile(r"->\s*trigger\s*\("),
    role="provider",
    transport=_TRANSPORT,
    label="Pusher::trigger",
    extensions=PHP,
    name=_FIRST,
    requires=("Pusher",),
)
_ANONYMOUS = SocketCall(
    head=re.compile(r"Broadcast::(?P<kind>on|private|presence)\s*\("),
    role="provider",
    transport=_TRANSPORT,
    label="Broadcast::on",
    extensions=PHP,
    name=_FIRST,
    requires=("Broadcast::",),
)
_ECHO_LISTEN = SocketCall(
    head=_ECHO_RE,
    role="consumer",
    transport=_TRANSPORT,
    label="Echo.listen",
    extensions=JS_TS,
    name=_FIRST,
    requires=("cho",),
)
_USE_ECHO = SocketCall(
    head=re.compile(r"\b(?P<kind>useEcho(?:Public|Presence)?)\s*(?:<[^>()]*>)?\s*\("),
    role="consumer",
    transport=_TRANSPORT,
    label="useEcho",
    extensions=JS_TS,
    name=_FIRST,
    requires=("useEcho",),
)
_PUSHER_SUBSCRIBE = SocketCall(
    head=re.compile(r"\.\s*subscribe\s*\("),
    role="consumer",
    transport=_TRANSPORT,
    label="channel.bind",
    extensions=JS_TS,
    name=_FIRST,
    requires=("pusher-js",),
)


def _channels(args: list[str], arg: Arg, strings: FileStrings, prefix: str) -> list[str]:
    """The channels *arg* names, holes as ``{param}``, with the wire *prefix*."""
    scopes, _ = strings.resolve(args, arg, channel_scope)
    return [prefix + scope for scope in scopes]


def _names(args: list[str], arg: Arg, strings: FileStrings) -> list[str]:
    """Event names as the wire carries them: a source ``\\\\`` is one backslash."""
    values, _ = strings.resolve(args, arg)
    return [unescape_backslashes(v) for v in values]


def _echo_event(name: str) -> str:
    """The wire name Echo's formatter gives *name*."""
    if name[:1] in (".", "\\"):
        return name[1:]
    return f"{_ECHO_NAMESPACE}.{name}".replace(".", "\\")


class LaravelBroadcastDialect:
    """The PHP side: broadcast events, anonymous broadcasts, ``$pusher->trigger``."""

    name = "laravel-broadcast"
    extensions = PHP

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        strings = file_strings(ctx)
        if strings is None:
            return []
        out: list[Contract] = []
        if "broadcastOn" in content and "extends Notification" not in content:
            out.extend(self._event_class(ctx, strings))
        for call, args, m, _s in call_sites(ctx, (_PUSHER_TRIGGER, _ANONYMOUS), strings):
            if call is _ANONYMOUS:
                prefix = _ANONYMOUS_PREFIX[m.group("kind")]
                close = match_paren(content, m.end() - 1)
                named = next((ln for ln in call_chain(content, close) if ln.name == "as"), None)
                events = _names(named.args, _FIRST, strings) if named else ["AnonymousEvent"]
            else:
                prefix, events = "", _names(args, _SECOND, strings)
            channels = _channels(args, _FIRST, strings, prefix)
            out.extend(event_contracts(ctx, call, channels, events, m.start()))
        return out

    @staticmethod
    def _event_class(ctx: ScanContext, strings: FileStrings) -> list[Contract]:
        content = ctx.content
        on = _BROADCAST_ON_RE.search(content)
        cls = PHP_CLASS_DECL_RE.search(content)
        if on is None or cls is None:
            return []
        close = match_paren(content, on.end() - 1, closer="}")
        body = content[on.end() : close] if close > 0 else ""
        channels: list[str] = []
        for m in _NEW_CHANNEL_RE.finditer(body):
            args = call_arguments(body, m.end() - 1) or []
            channels.extend(_channels(args, _FIRST, strings, _PHP_CHANNEL_PREFIX[m.group("cls")]))
        alias = _BROADCAST_AS_RE.search(content)
        if alias is not None:
            events = _names([alias.group("value")], _FIRST, strings)
        else:
            events = [qualify_php_name(cls.group("cls"), file_namespace(content), {})]
        return event_contracts(
            ctx, _BROADCAST_EVENT, channels, events, cls.start("cls"), label=cls.group("cls")
        )


class EchoDialect:
    """The JS side: Laravel Echo, its ``useEcho`` hooks, and pusher-js."""

    name = "echo"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        out: list[Contract] = []
        for call, args, m, strings in call_sites(ctx, (_ECHO_LISTEN, _USE_ECHO, _PUSHER_SUBSCRIBE)):
            kind = m.groupdict().get("kind") or ""
            if call is _PUSHER_SUBSCRIBE:
                channels = _channels(args, _FIRST, strings, "")
                close = match_paren(content, m.end() - 1)
                events = [
                    e
                    for ln in call_chain(content, close)
                    if ln.name == "bind"
                    for e in _names(ln.args, _FIRST, strings)
                ]
                events.extend(self._bound_later(content, m.start(), close, strings))
            elif call is _USE_ECHO:
                channels = _channels(args, _FIRST, strings, _ECHO_PREFIX[kind])
                events = [_echo_event(e) for e in _names(args, _SECOND, strings)]
            else:
                channels = _channels(args, _FIRST, strings, _ECHO_PREFIX[kind])
                close = match_paren(content, m.end() - 1)
                events = [
                    _echo_event(e)
                    for ln in call_chain(content, close)
                    if ln.name == "listen"
                    for e in _names(ln.args, _FIRST, strings)
                ]
            out.extend(event_contracts(ctx, call, channels, events, m.start()))
        return out

    @staticmethod
    def _bound_later(content: str, start: int, close: int, strings: FileStrings) -> list[str]:
        """``const ch = pusher.subscribe(c); ch.bind('e')``: the events bound on the variable.

        Only binds after this assignment and before the variable is assigned
        again, so two subscriptions reusing one name keep their own events.
        """
        line_start = content.rfind("\n", 0, start) + 1
        assigned = _ASSIGNED_RE.search(content, line_start, start)
        if assigned is None:
            return []
        var = re.escape(assigned.group("var"))
        boundary = r"(?<![\w$.])"
        again = re.compile(boundary + var + r"\s*=(?!=)").search(content, close)
        end = again.start() if again else len(content)
        out: list[str] = []
        for m in re.compile(boundary + var + r"\s*\??\.\s*bind\s*\(").finditer(content, close, end):
            out.extend(_names(call_arguments(content, m.end() - 1) or [], _FIRST, strings))
        return out


LARAVEL_BROADCAST = LaravelBroadcastDialect()
ECHO = EchoDialect()

__all__ = ["ECHO", "LARAVEL_BROADCAST"]
