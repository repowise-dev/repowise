"""Helper tasks a tool call starts beside its main work, ended before the call returns.

A helper cancelled in the middle of a database query closes its connection as
it unwinds. A call that only cancels its helpers returns before that close
ends, and a server shut down right then disposes the engine under the close:
both wait on the connection forever. So a call cancels its helpers and waits
for them to unwind before it returns.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from collections.abc import Callable, Collection, Coroutine
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: The helpers started under the running tool call, when one is collecting.
_CALL_TASKS: contextvars.ContextVar[set[asyncio.Future] | None] = contextvars.ContextVar(
    "repowise_call_tasks", default=None
)

#: Upper bound on the wait for cancelled helpers. An unwind is a connection
#: close, so it normally takes milliseconds; one stuck past this is left running.
UNWIND_TIMEOUT_S = 10.0


def start_call_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Run *coro* beside the current tool call, which ends it before returning.

    Outside a call wrapped in :func:`ends_call_tasks` the task is not tracked
    and its starter owns it.
    """
    task = asyncio.create_task(coro)
    tasks = _CALL_TASKS.get()
    if tasks is not None:
        tasks.add(task)
    return task


def ends_call_tasks(fn: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Any]:
    """Wrap tool *fn* so every helper it starts is cancelled and awaited when it ends."""

    @functools.wraps(fn)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        tasks: set[asyncio.Future] = set()
        token = _CALL_TASKS.set(tasks)
        try:
            return await fn(*args, **kwargs)
        finally:
            _CALL_TASKS.reset(token)
            await cancel_and_wait(tasks)

    return wrapped


async def cancel_and_wait(
    tasks: Collection[asyncio.Future], timeout: float = UNWIND_TIMEOUT_S
) -> None:
    """Cancel *tasks* and wait up to *timeout* seconds for them to unwind.

    The wait holds even when the caller is itself being cancelled, as a server
    shutting down cancels its requests, since that is when the engine is closed
    next. A helper that starts another while unwinding has that one ended too.
    """
    if all(task.done() for task in tasks):
        _retrieve(tasks)
        return
    import anyio  # the server's MCP transport runs on it; loaded only when needed

    # The shield holds against anyio cancel scopes, which is how the MCP
    # transport cancels a request. A second native cancel of the caller still
    # ends the wait early; nothing in the server sends one.
    with anyio.move_on_after(timeout, shield=True):
        while pending := [task for task in tasks if not task.done()]:
            for task in pending:
                # A second cancel would interrupt the unwind this waits for.
                if not _cancel_requested(task):
                    task.cancel()
            await asyncio.wait(pending)
    left = sum(not task.done() for task in tasks)
    if left:
        log.warning("call_helpers_still_unwinding", count=left, timeout_s=timeout)
    _retrieve(tasks)


def _cancel_requested(task: asyncio.Future) -> bool:
    """Whether *task* was already asked to cancel, by its starter or an earlier pass."""
    cancelling = getattr(task, "cancelling", None)  # Tasks have it; plain futures do not
    return bool(cancelling()) if cancelling is not None else task.cancelled()


def _retrieve(tasks: Collection[asyncio.Future]) -> None:
    """Read each failed helper's exception so asyncio does not log it as never retrieved."""
    for task in tasks:
        if task.done() and not task.cancelled():
            task.exception()
