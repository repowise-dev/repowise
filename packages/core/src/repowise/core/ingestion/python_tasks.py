"""Python fire-and-forget tasks: a coroutine its caller hands off and never waits for.

``asyncio.create_task(job())`` whose task is dropped, or only kept in a set so
it is not garbage collected, runs beside its caller: the caller does not pay
for it and does not repeat it. Anything less certain reads as the caller's own
work: a task awaited, gathered, returned or passed on; a container of tasks
used for anything but adding and removing; a scheduler other than ``asyncio``
itself (a ``TaskGroup`` the caller waits for looks the same as one it does not).

Read within one function and matched by name, so the answer errs towards
"the caller pays", which keeps a cost visible rather than hiding it.
"""

from __future__ import annotations

from collections.abc import Iterator

from tree_sitter import Node

from .extractors import node_text

#: ``asyncio`` calls that schedule the coroutine they are handed.
_SCHEDULERS = frozenset({"create_task", "ensure_future", "run_coroutine_threadsafe"})
#: Container methods that only keep a task alive or let it go.
_KEEPS = frozenset({"add", "append", "appendleft", "extend", "discard", "remove", "pop"})
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


def _method(call: Node, src: str) -> tuple[str | None, str]:
    """``(receiver text, method name)`` of a call; ``None`` receiver for a bare call."""
    function = call.child_by_field_name("function")
    if function is None:
        return None, ""
    if function.type != "attribute":
        return None, node_text(function, src)
    receiver = function.child_by_field_name("object")
    name = function.child_by_field_name("attribute")
    return (
        node_text(receiver, src) if receiver is not None else None,
        node_text(name, src) if name is not None else "",
    )


def _scheduler_of(site: Node, src: str) -> Node | None:
    """The ``asyncio`` scheduling call *site* is the first argument of, else ``None``.

    Only ``asyncio.create_task(...)`` or a bare ``create_task(...)``: a task
    group or a loop object may be one the caller waits for.
    """
    args = site.parent
    outer = args.parent if args is not None and args.type == "argument_list" else None
    if outer is None or outer.type != "call":
        return None
    receiver, name = _method(outer, src)
    if name not in _SCHEDULERS or receiver not in (None, "asyncio"):
        return None
    first = next((c for c in args.children if c.is_named), None)
    return outer if first == site else None


def _scope_of(node: Node) -> Node:
    cur = node
    while cur.parent is not None and cur.type not in _SCOPES:
        cur = cur.parent
    return cur


def _assigned_holder(assignment: Node, value: Node, scheduler: Node, src: str) -> tuple[str, str | None]:
    left = assignment.child_by_field_name("left")
    if left is not None and left.type == "subscript":
        return "container", node_text(left.child_by_field_name("value"), src)
    if left is not None and left.type in ("identifier", "attribute"):
        return ("task" if value == scheduler else "container"), node_text(left, src)
    return "used", None


def _holder(scheduler: Node, src: str) -> tuple[str, str | None]:
    """How the task is held: ``("dropped", None)``, ``("task", name)`` for a name
    bound to it, ``("container", name)`` for a container it is put in, or
    ``("used", None)`` when anything else consumes it."""
    value = scheduler
    while value.parent is not None and value.parent.type in _CONTAINER_LITERALS:
        value = value.parent
    parent = value.parent
    if parent is None:
        return "used", None
    if parent.type == "expression_statement" and value == scheduler:
        return "dropped", None
    if parent.type == "assignment" and parent.child_by_field_name("right") == value:
        return _assigned_holder(parent, value, scheduler, src)
    store = parent.parent if parent.type == "argument_list" else None
    if store is not None and store.type == "call":
        receiver, name = _method(store, src)
        if name in _KEEPS and receiver:
            return "container", receiver
    return "used", None


def _uses(scope: Node, name: str, src: str) -> Iterator[Node]:
    """Every use of *name* in *scope* except a plain rebinding of it."""
    for node in _walk(scope):
        if node.type not in ("identifier", "attribute") or node_text(node, src) != name:
            continue
        parent = node.parent
        if parent is not None and parent.type == "assignment" and parent.child_by_field_name("left") == node:
            continue
        yield node


def _method_on(node: Node, methods: frozenset[str], src: str) -> bool:
    """``node.<method>`` for one of *methods* (called or passed as a callback)."""
    parent = node.parent
    if parent is None or parent.type != "attribute" or parent.child_by_field_name("object") != node:
        return False
    return node_text(parent.child_by_field_name("attribute"), src) in methods


def _kept_in(node: Node, src: str) -> str | None:
    """The container a task use puts it in: ``c.add(task)`` or ``c[k] = task``."""
    parent = node.parent
    if parent is None:
        return None
    if parent.type == "argument_list" and parent.parent is not None:
        receiver, name = _method(parent.parent, src)
        return receiver if name in _KEEPS else None
    if parent.type == "assignment" and parent.child_by_field_name("right") == node:
        left = parent.child_by_field_name("left")
        if left is not None and left.type == "subscript":
            return node_text(left.child_by_field_name("value"), src)
    return None


def _task_containers(scope: Node, name: str, src: str) -> set[str] | None:
    """The containers the task *name* is kept in, or ``None`` when any use of
    it might wait for or consume it."""
    containers: set[str] = set()
    for node in _uses(scope, name, src):
        if _method_on(node, _TASK_METHODS, src):
            continue
        kept = _kept_in(node, src)
        if kept is None:
            return None
        containers.add(kept)
    return containers


def _stored_into(node: Node) -> bool:
    """``node[key] = ...``: the container is written, not read."""
    target = node.parent
    store = target.parent if target is not None and target.type == "subscript" else None
    return (
        store is not None
        and target.child_by_field_name("value") == node
        and store.type == "assignment"
        and store.child_by_field_name("left") == target
    )


def _only_kept(scope: Node, container: str, src: str) -> bool:
    """Whether *container* is only added to and removed from in *scope*."""
    return all(
        _method_on(node, _KEEPS, src) or _stored_into(node)
        for node in _uses(scope, container, src)
    )


def is_fire_and_forget(site: Node, src: str) -> bool:
    """Whether the Python call at *site* is a coroutine ``asyncio`` schedules
    and its function provably never waits for."""
    scheduler = _scheduler_of(site, src)
    if scheduler is None:
        return False
    kind, name = _holder(scheduler, src)
    if kind == "dropped":
        return True
    if kind == "used" or name is None:
        return False
    scope = _scope_of(scheduler)
    containers = {name} if kind == "container" else _task_containers(scope, name, src)
    if containers is None:
        return False
    return all(_only_kept(scope, container, src) for container in containers)


__all__ = ["is_fire_and_forget"]
