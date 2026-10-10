"""Python fire-and-forget tasks: a coroutine its caller hands off and never waits for.

``asyncio.create_task(job())`` whose task is dropped, or only kept in a set so
it is not garbage collected, runs beside its caller: the caller does not pay
for it and does not repeat it. A task the same function awaits, gathers or
hands to a ``TaskGroup`` is the caller's own work and is not fire-and-forget.

Matched by name and read within one function: a task returned, or awaited by
another function through a container, still reads as the caller's work here,
which keeps the caller charged rather than hiding a cost.
"""

from __future__ import annotations

from collections.abc import Iterator

from tree_sitter import Node

from .extractors import node_text

#: Calls that schedule the coroutine they are handed instead of awaiting it.
_SCHEDULERS = frozenset({"create_task", "ensure_future", "run_coroutine_threadsafe"})
#: Calls whose arguments are tasks the caller waits for.
_COLLECTORS = frozenset({"gather", "wait", "wait_for", "as_completed", "shield"})
#: Container methods that only keep a task alive.
_STORES = frozenset({"add", "append", "appendleft", "extend", "discard", "remove", "setdefault"})
#: Task methods that neither wait for nor consume the result.
_TASK_METHODS = frozenset(
    {"add_done_callback", "remove_done_callback", "cancel", "set_name", "get_name", "done"}
)
_SCOPES = frozenset({"function_definition", "lambda"})
_CONTAINER_LITERALS = frozenset(
    {"list", "tuple", "set", "list_comprehension", "set_comprehension", "generator_expression"}
)


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def _callee_name(call: Node, src: str) -> str:
    function = call.child_by_field_name("function")
    if function is not None and function.type == "attribute":
        function = function.child_by_field_name("attribute")
    return node_text(function, src) if function is not None else ""


def _receiver(call: Node, src: str) -> str | None:
    function = call.child_by_field_name("function")
    if function is None or function.type != "attribute":
        return None
    receiver = function.child_by_field_name("object")
    return node_text(receiver, src) if receiver is not None else None


def _scheduler_of(site: Node, src: str) -> Node | None:
    """The scheduling call *site* is the first argument of, else ``None``."""
    args = site.parent
    outer = args.parent if args is not None and args.type == "argument_list" else None
    if outer is None or outer.type != "call" or _callee_name(outer, src) not in _SCHEDULERS:
        return None
    first = next((c for c in args.children if c.is_named), None)
    return outer if first == site else None


def _scope_of(node: Node) -> Node:
    cur = node
    while cur.parent is not None and cur.type not in _SCOPES:
        cur = cur.parent
    return cur


def _in_task_group(scheduler: Node, scope: Node, src: str) -> bool:
    """``async with TaskGroup() as tg: tg.create_task(...)``: the group awaits it."""
    receiver = _receiver(scheduler, src)
    cur = scheduler.parent
    while receiver and cur is not None and cur != scope:
        if cur.type == "with_statement" and any(c.type == "async" for c in cur.children):
            clause = next((c for c in cur.children if c.type == "with_clause"), None)
            aliases = {
                node_text(n, src)
                for n in (_walk(clause) if clause is not None else ())
                if n.type == "as_pattern_target"
            }
            if receiver in aliases:
                return True
        cur = cur.parent
    return False


def _holder(scheduler: Node, src: str) -> tuple[str, str | None]:
    """How the task is held: ``("dropped", None)``, ``("task", name)`` for a name
    bound to the task, ``("container", name)`` for a container it is put in, or
    ``("used", None)`` when anything else consumes it (``await``, ``return``)."""
    node = scheduler
    while node.parent is not None and node.parent.type in _CONTAINER_LITERALS:
        node = node.parent
    parent = node.parent
    if parent is None:
        return "used", None
    if parent.type == "expression_statement" and node == scheduler:
        return "dropped", None
    if parent.type == "assignment" and parent.child_by_field_name("right") == node:
        left = parent.child_by_field_name("left")
        if left is not None and left.type == "subscript":
            return "container", node_text(left.child_by_field_name("value"), src)
        if left is not None and left.type in ("identifier", "attribute"):
            kind = "task" if node == scheduler else "container"
            return kind, node_text(left, src)
        return "used", None
    store = parent.parent if parent.type == "argument_list" else None
    if store is not None and store.type == "call" and _callee_name(store, src) in _STORES:
        receiver = _receiver(store, src)
        return ("container", receiver) if receiver else ("used", None)
    return "used", None


def _waited(scope: Node, name: str, src: str) -> bool:
    """Whether *name* is awaited or collected anywhere in *scope*."""
    for node in _walk(scope):
        if node.type not in ("identifier", "attribute") or node_text(node, src) != name:
            continue
        cur = node.parent
        while cur is not None and cur != scope:
            if cur.type == "await":
                return True
            if cur.type == "call" and _callee_name(cur, src) in _COLLECTORS:
                return True
            cur = cur.parent
    return False


def _task_uses_only_keep(scope: Node, name: str, src: str) -> tuple[bool, set[str]]:
    """Whether every use of the task *name* only keeps or inspects it, and the
    containers it is kept in."""
    containers: set[str] = set()
    for node in _walk(scope):
        if node.type not in ("identifier", "attribute") or node_text(node, src) != name:
            continue
        parent = node.parent
        if parent is None:
            continue
        if parent.type == "assignment" and parent.child_by_field_name("left") == node:
            continue
        if parent.type == "attribute" and parent.child_by_field_name("object") == node:
            if node_text(parent.child_by_field_name("attribute"), src) in _TASK_METHODS:
                continue
            return False, containers
        if parent.type == "argument_list" and parent.parent is not None:
            call = parent.parent
            if _callee_name(call, src) in _STORES and (receiver := _receiver(call, src)):
                containers.add(receiver)
                continue
            return False, containers
        if parent.type == "assignment" and parent.child_by_field_name("right") == node:
            left = parent.child_by_field_name("left")
            if left is not None and left.type == "subscript":
                containers.add(node_text(left.child_by_field_name("value"), src))
                continue
        if parent.type != "attribute":
            return False, containers
    return True, containers


def is_fire_and_forget(site: Node, src: str) -> bool:
    """Whether the Python call at *site* is a coroutine handed to a task
    scheduler whose task its function never waits for."""
    scheduler = _scheduler_of(site, src)
    if scheduler is None:
        return False
    scope = _scope_of(scheduler)
    if _in_task_group(scheduler, scope, src):
        return False
    kind, name = _holder(scheduler, src)
    if kind == "dropped":
        return True
    if kind == "used" or name is None:
        return False
    held = {name}
    if kind == "task":
        only_kept, containers = _task_uses_only_keep(scope, name, src)
        if not only_kept:
            return False
        held |= containers
    return not any(_waited(scope, each, src) for each in held)


__all__ = ["is_fire_and_forget"]
