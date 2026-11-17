"""C# integration events: an event bus that routes each message by its type's short name.

The eShop event bus, and the many services built from it, publish an
``IntegrationEvent`` subclass and route it by ``GetType().Name``: the
RabbitMQ routing key is the short type name, whatever namespace declares it.
Services share no contract assembly; each re-declares the record it sends or
receives. So the contract id is ``topic::<short type name>``, which is
exactly what the transport compares, and two same-named types meet on the
wire as they meet here.

Three facts have to agree before an end is recorded:

- An event type is a class or record the repo declares deriving from a base in
  :data:`EVENT_BASES`, directly or through another event type.
- A publisher is ``new T(...)`` of an event type that reaches a publish call:
  passed inline (``PublishAsync(new T(..))``) or through the variable it was
  assigned to, inside the block that declares the variable. A publish call is
  ``PublishAsync`` / ``Publish``, or a method the repo declares with a
  parameter of a base type whose name says it publishes, sends or saves (an
  outbox's ``AddAndSaveEventAsync(IntegrationEvent evt)``); a
  ``Log(IntegrationEvent e)`` is not one.
- A subscriber is a subscription row below. A type parameter (``where T :``)
  is never an event, and a name the repo declares as some other type is not
  one either.

The facts span files (the base type, the outbox method, the record), so this
dialect is a repo pass. A ceiling: an event declared only in a shared
contract repo is read on the subscriber side but has no publisher, since the
publishing repo does not declare it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from repowise.core.workspace.contracts import TOPIC_KIND_TOPIC
from repowise.core.workspace.origins import ContractOrigin

from ..calls import code_matches
from ..dialect import rows_gate
from ..langs import CSHARP
from .dialect import topic_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_BROKER = "integration_event"

#: Base types whose subclasses are integration events.
EVENT_BASES: tuple[str, ...] = ("IntegrationEvent",)

#: Calls that publish their argument whatever the repo declares.
_PUBLISH_CALLS = frozenset({"PublishAsync", "Publish"})


@dataclass(frozen=True)
class _Subscription:
    """One subscription shape; ``event`` is the group naming the event type.

    ``declared`` refuses an event type the repo does not declare (a call name
    too common to trust alone). ``fallback`` reads the shape only for an event
    no other row subscribes in the repo: a handler class is how a subscription
    is wired, but the registration is where it is made.
    """

    head: re.Pattern[str]
    label: str
    origin: ContractOrigin
    requires: tuple[str, ...]
    declared: bool = False
    fallback: bool = False


_TYPE = r"(?:global::)?(?P<event>[A-Za-z_][\w.]*)"
# The handler argument: `XHandler`, or `IIntegrationEventHandler<X>` itself.
_HANDLER = r"[\w.:]+(?:\s*<[^<>()]*>)?"

SUBSCRIPTIONS: tuple[_Subscription, ...] = (
    _Subscription(
        re.compile(rf"\.\s*AddSubscription\s*<\s*{_TYPE}\s*,\s*{_HANDLER}\s*>\s*\("),
        "AddSubscription",
        ContractOrigin.CLIENT_CALL,
        ("AddSubscription",),
    ),
    # eShopOnContainers' `eventBus.Subscribe<T, TH>()`.
    _Subscription(
        re.compile(rf"\.\s*Subscribe\s*<\s*{_TYPE}\s*,\s*{_HANDLER}\s*>\s*\("),
        "Subscribe",
        ContractOrigin.CLIENT_CALL,
        (".Subscribe",),
        declared=True,
    ),
    _Subscription(
        re.compile(rf"\bIIntegrationEventHandler\s*<\s*{_TYPE}\s*>"),
        "IIntegrationEventHandler",
        ContractOrigin.CLIENT_CALL,
        ("IIntegrationEventHandler",),
        fallback=True,
    ),
)

# `record X(int A, List<B> C) : IntegrationEvent;`, `class X : Base, IFoo {`.
_TYPE_DECL_RE = re.compile(
    r"\b(?:class|record(?:\s+class|\s+struct)?|struct)\s+(?P<name>[A-Za-z_]\w*)"
    r"\s*(?:<[^<>]*>)?\s*(?:\((?:[^()]|\([^()]*\))*\))?"
    r"\s*(?::\s*(?P<bases>[^{;]*?))?\s*(?=\{|;|\bwhere\b)"
)
_BASE_NAME_RE = re.compile(r"(?:global::)?(?:\w+\.)*(\w+)")
_TYPE_PARAM_RE = re.compile(r"\bwhere\s+(\w+)\s*:|<\s*(?:in|out)\s+(\w+)")
# A parameter of an event base type, `AddAndSaveEventAsync(IntegrationEvent evt)`;
# the method it belongs to is the name before its `(`.
_BASE_PARAM_RE = re.compile(
    r"\b(?:" + "|".join(EVENT_BASES) + r")\??\s+@?[A-Za-z_]\w*\s*[,)]"
)
_METHOD_NAME_RE = re.compile(r"\b([A-Za-z_]\w*)\s*(?:<[^<>()]*>)?\s*$")
# A method taking the base type publishes only if its name says so; a logger does not.
_SINK_VERBS = ("Publish", "Send", "Save", "Enqueue", "Raise", "Dispatch", "Emit")
_SINK_NAME_RE = re.compile("|".join(_SINK_VERBS))
# Words that precede a name without declaring it (`return e;`, `await e;`).
_NOT_A_TYPE = r"(?!(?:return|await|throw|yield|else|new|in|out|ref|is|as|case|goto)\b)"
_NEW_RE = re.compile(r"\bnew\s+(?:global::)?(?:\w+\.)*(?P<type>[A-Za-z_]\w*)\s*[({]")
# What precedes `new` in its statement: `var x = `, `x = cond ? (Base)`, `Call(`.
_ASSIGNED_RE = re.compile(r"(?:^|[\s(])@?(?P<var>[A-Za-z_]\w*)\s*=(?![=>])")
_INLINE_CALL_RE = re.compile(r"\b(?P<call>[A-Za-z_]\w*)\s*\(\s*(?:\([\w.]+\)\s*)?$")
_STATEMENT_START = re.compile(r"[;{}]")
_MAX_STATEMENT = 400


@dataclass
class _Construction:
    ctx: ScanContext
    offset: int
    event: str
    var: str | None
    call: str | None


@dataclass
class _Subscribed:
    ctx: ScanContext
    offset: int
    event: str
    row: _Subscription
    type_params: frozenset[str]


@dataclass
class _EventPass:
    bases: dict[str, set[str]] = field(default_factory=dict)
    sinks: set[str] = field(default_factory=set)
    constructions: list[_Construction] = field(default_factory=list)
    subscriptions: list[_Subscribed] = field(default_factory=list)

    def scan(self, ctx: ScanContext) -> None:
        content = ctx.content
        for m in code_matches(ctx, _TYPE_DECL_RE):
            bases = {
                b.group(1)
                for part in (m.group("bases") or "").split(",")
                if (b := _BASE_NAME_RE.match(part.strip()))
            }
            self.bases.setdefault(m.group("name"), set()).update(bases)
        if ctx.has_any(EVENT_BASES):
            for m in code_matches(ctx, _BASE_PARAM_RE):
                head = content[max(0, m.start() - _MAX_STATEMENT) : m.start()]
                open_paren = head.rfind("(")
                if open_paren < 0 or ")" in head[open_paren:]:
                    continue
                name = _METHOD_NAME_RE.search(head[:open_paren])
                if name and _SINK_NAME_RE.search(name.group(1)):
                    self.sinks.add(name.group(1))
        if "new" in content:
            for m in code_matches(ctx, _NEW_RE):
                self.constructions.append(self._construction(ctx, m))
        type_params: frozenset[str] | None = None
        for row in SUBSCRIPTIONS:
            if not ctx.has_any(row.requires):
                continue
            for m in code_matches(ctx, row.head):
                if type_params is None:
                    type_params = frozenset(
                        a or b for a, b in _TYPE_PARAM_RE.findall(content)
                    )
                event = m.group("event").rpartition(".")[2]
                self.subscriptions.append(_Subscribed(ctx, m.start(), event, row, type_params))

    @staticmethod
    def _construction(ctx: ScanContext, m: re.Match[str]) -> _Construction:
        content = ctx.content
        start = m.start()
        window = content[max(0, start - _MAX_STATEMENT) : start]
        stops = list(_STATEMENT_START.finditer(window))
        statement = window[stops[-1].end() :] if stops else window
        inline = _INLINE_CALL_RE.search(statement)
        assigned = _ASSIGNED_RE.search(statement)
        return _Construction(
            ctx,
            start,
            m.group("type"),
            assigned.group("var") if assigned else None,
            inline.group("call") if inline else None,
        )

    def _events(self) -> set[str]:
        events: set[str] = set()
        known = set(EVENT_BASES)
        changed = True
        while changed:
            changed = False
            for name, bases in self.bases.items():
                if name not in events and bases & known:
                    events.add(name)
                    known.add(name)
                    changed = True
        return events

    def finish(self) -> list[Contract]:
        events = self._events()
        sinks = _PUBLISH_CALLS | self.sinks
        out: list[Contract] = []
        for c in self.constructions:
            if c.event not in events:
                continue
            if c.call in sinks:
                origin = ContractOrigin.CLIENT_CALL
                label = c.call
            elif c.var and _passes_to_sink(c, sinks):
                origin = ContractOrigin.DATAFLOW
                label = "new"
            else:
                continue
            out.append(_contract(c.ctx, c.offset, c.event, "provider", label, origin))
        subscribed = {
            s.event for s in self.subscriptions if not s.row.fallback and self._accepts(s, events)
        }
        for s in self.subscriptions:
            if (s.row.fallback and s.event in subscribed) or not self._accepts(s, events):
                continue
            out.append(_contract(s.ctx, s.offset, s.event, "consumer", s.row.label, s.row.origin))
        return out

    def _accepts(self, s: _Subscribed, events: set[str]) -> bool:
        if s.event in events:
            return True
        if s.row.declared or s.event in s.type_params or s.event in EVENT_BASES:
            return False
        # Declared here as something other than an event: not this bus's message.
        return s.event not in self.bases


def _passes_to_sink(c: _Construction, sinks: set[str]) -> bool:
    """Whether *c*'s variable reaches a publish call later in the block declaring it.

    The block is the one around the variable's nearest declaration above the
    construction, so a same-named variable in another method is another value.
    """
    content = c.ctx.content
    var = re.escape(c.var or "")
    declared = re.compile(rf"(?<![\w.]){_NOT_A_TYPE}[A-Za-z_][\w.<>,?\[\]]*\s+@?{var}\s*[=;]")
    scope = c.offset
    for m in declared.finditer(content, 0, c.offset):
        scope = m.start()
    end = _block_end(content, scope)
    names = "|".join(sorted(map(re.escape, sinks)))
    call = re.compile(rf"\b(?:{names})\s*\(\s*(?:[^()]*,\s*)?@?{var}\s*[,)]")
    return any(not c.ctx.in_comment(m.start()) for m in call.finditer(content, c.offset, end))


def _block_end(content: str, start: int) -> int:
    """The offset of the brace closing the block *start* sits in (the end of file if none)."""
    depth = 0
    for i in range(start, len(content)):
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return i
    return len(content)


def _contract(
    ctx: ScanContext, offset: int, event: str, role: str, label: str, origin: ContractOrigin
) -> Contract:
    return topic_contract(
        ctx,
        name=event,
        broker=_BROKER,
        kind=TOPIC_KIND_TOPIC,
        role=role,
        label=label,
        origin=origin,
        offset=offset,
    )


class CSharpEventDialect:
    """Integration events published and subscribed through a C# event bus."""

    name = _BROKER
    extensions = CSHARP
    calls = SUBSCRIPTIONS
    # A publisher file may name neither the base nor `Publish`, only an outbox method.
    requires = (*EVENT_BASES, *rows_gate(SUBSCRIPTIONS), *_SINK_VERBS)

    def repo_pass(self) -> _EventPass:
        return _EventPass()

    def extract(self, ctx: ScanContext) -> list[Contract]:
        """One file on its own (the extractor uses :meth:`repo_pass` instead)."""
        repo_pass = self.repo_pass()
        repo_pass.scan(ctx)
        return repo_pass.finish()


CSHARP_EVENTS = CSharpEventDialect()

__all__ = ["CSHARP_EVENTS", "EVENT_BASES", "SUBSCRIPTIONS"]
