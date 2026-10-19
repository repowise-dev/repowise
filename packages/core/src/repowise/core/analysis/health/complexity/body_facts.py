"""One rule for what a node says about the function holding it.

Two walks read it. The complexity walk counts a whole function's facts on its
own node visits (``cyclomatic._walk_function_body``), and the Extract Method
span walk counts a candidate span's (``dataflow.slice._span_metrics``). Both
call :func:`step` per node, so an ``await`` under an ``async`` block, a
``return`` inside a lambda or an exit macro means the same thing to each.

A lambda is a scope of its own: its awaits, yields and exits are its own, but
it shares the instance, so receiver references inside it still count.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

IN_LAMBDA = 1
AWAIT_BLOCKED = 2


@dataclass(frozen=True, slots=True)
class FactKinds:
    """The node kinds :func:`step` counts. An empty set counts nothing.

    ``jumps`` are what keeps a span from being lifted out (break, continue,
    yield, return, raise); ``exits`` what leaves the function (return, raise).
    ``exit_macros`` (macro kinds, macro names) count as both. ``receiver``
    are the kinds a receiver reference can be, read only with a sink.
    """

    decisions: frozenset[str] = frozenset()
    jumps: frozenset[str] = frozenset()
    exits: frozenset[str] = frozenset()
    exit_macros: tuple[frozenset[str], frozenset[str]] = (frozenset(), frozenset())
    yields: frozenset[str] = frozenset()
    awaits: frozenset[str] = frozenset()
    await_scopes: frozenset[str] = frozenset()
    lambdas: frozenset[str] = frozenset()
    receiver: frozenset[str] = frozenset()
    # Every kind above: a walk that sees every node skips the rest with one lookup.
    watched: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        watched = (
            self.decisions
            | self.jumps
            | self.exits
            | self.exit_macros[0]
            | self.yields
            | self.awaits
            | self.await_scopes
            | self.lambdas
            | self.receiver
        )
        object.__setattr__(self, "watched", watched)


@dataclass(slots=True)
class BodyTally:
    """What :func:`step` counted. ``on_receiver`` sees each receiver-kind node."""

    kinds: FactKinds
    on_receiver: Callable[[Node], object] | None = None
    decisions: int = 0
    jump: bool = False
    exits: int = 0
    yields: int = 0
    awaits: bool = False


def is_exit_macro(node: Node, exit_macros: tuple[frozenset[str], frozenset[str]]) -> bool:
    """A macro whose name is in *exit_macros*, matched by its last segment:
    ``bail`` in ``anyhow::bail!``."""
    kinds, names = exit_macros
    if node.type not in kinds:
        return False
    macro = node.child_by_field_name("macro")
    name = macro.child_by_field_name("name") or macro if macro is not None else None
    return name is not None and bool(name.text) and name.text.decode("utf-8", "replace") in names


def is_exit(node: Node, kinds: FactKinds) -> bool:
    """*node* leaves the function: an exit kind or an exit macro."""
    return node.type in kinds.exits or is_exit_macro(node, kinds.exit_macros)


def step(tally: BodyTally, node: Node, scope: int) -> int:
    """Count *node* into *tally*; the scope its children are visited in."""
    kinds = tally.kinds
    t = node.type
    if tally.on_receiver is not None and t in kinds.receiver:
        tally.on_receiver(node)
    if t in kinds.lambdas:
        return scope | IN_LAMBDA
    if scope & IN_LAMBDA:
        return scope
    tally.decisions += t in kinds.decisions
    macro = is_exit_macro(node, kinds.exit_macros)
    tally.exits += macro or t in kinds.exits
    tally.jump = tally.jump or macro or t in kinds.jumps
    tally.yields += t in kinds.yields
    if not scope & AWAIT_BLOCKED and t in kinds.awaits:
        tally.awaits = True
    return scope | AWAIT_BLOCKED if t in kinds.await_scopes else scope


__all__ = [
    "AWAIT_BLOCKED",
    "IN_LAMBDA",
    "BodyTally",
    "FactKinds",
    "is_exit",
    "is_exit_macro",
    "step",
]
