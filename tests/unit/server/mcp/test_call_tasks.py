"""A tool call ends the helper tasks it starts before it returns."""

from __future__ import annotations

import asyncio
import gc

import pytest

from repowise.server.mcp_server._call_tasks import (
    cancel_and_wait,
    ends_call_tasks,
    start_call_task,
)


async def _slow_unwind(unwound: list[str], name: str, seconds: float = 0.05) -> None:
    """Block until cancelled, then take *seconds* to unwind, like a connection close."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.sleep(seconds))
        unwound.append(name)
        raise


async def test_a_call_returns_only_after_its_helpers_unwound() -> None:
    unwound: list[str] = []
    started: list[asyncio.Task] = []

    @ends_call_tasks
    async def tool() -> str:
        started.append(start_call_task(_slow_unwind(unwound, "helper")))
        await asyncio.sleep(0)
        return "reply"

    assert await tool() == "reply"
    assert unwound == ["helper"]
    assert started[0].cancelled()


async def test_a_call_that_raises_ends_its_helpers_too() -> None:
    unwound: list[str] = []

    @ends_call_tasks
    async def tool() -> None:
        start_call_task(_slow_unwind(unwound, "helper"))
        await asyncio.sleep(0)
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await tool()
    assert unwound == ["helper"]


async def test_a_cancelled_call_still_waits_for_its_helpers() -> None:
    """A server shutting down cancels its requests and then closes the engine."""
    unwound: list[str] = []
    entered = asyncio.Event()

    @ends_call_tasks
    async def tool() -> None:
        start_call_task(_slow_unwind(unwound, "helper"))
        entered.set()
        await asyncio.Event().wait()

    call = asyncio.create_task(tool())
    await entered.wait()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert unwound == ["helper"]


async def test_a_helper_started_while_unwinding_is_ended_too() -> None:
    unwound: list[str] = []

    async def spawning() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            start_call_task(_slow_unwind(unwound, "late"))
            await asyncio.sleep(0)
            raise

    @ends_call_tasks
    async def tool() -> None:
        start_call_task(spawning())
        await asyncio.sleep(0)

    await tool()
    assert unwound == ["late"]


async def test_the_wait_is_bounded() -> None:
    unwound: list[str] = []
    stuck = asyncio.create_task(_slow_unwind(unwound, "stuck", seconds=1))
    await asyncio.sleep(0)
    loop = asyncio.get_running_loop()
    began = loop.time()

    await cancel_and_wait([stuck], timeout=0.05)

    assert loop.time() - began < 0.5
    assert not stuck.done()
    await asyncio.wait({stuck})


async def test_a_helper_its_starter_cancelled_is_not_cancelled_again() -> None:
    """A second cancel would land inside the unwind, such as a connection close."""
    unwound: list[str] = []

    async def unwinds_once() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            try:
                await asyncio.sleep(0.05)  # unshielded: a second cancel cuts it short
            except asyncio.CancelledError:
                unwound.append("interrupted")
                raise
            unwound.append("closed")
            raise

    @ends_call_tasks
    async def tool() -> None:
        helper = start_call_task(unwinds_once())
        await asyncio.sleep(0)
        helper.cancel()  # the starter stops it early, as change risk does
        await asyncio.sleep(0)

    await tool()
    assert unwound == ["closed"]


async def test_a_failed_helper_is_not_reported_as_never_retrieved() -> None:
    async def fails() -> None:
        raise RuntimeError("helper failed")

    loop = asyncio.get_running_loop()
    reported: list[dict] = []
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    try:
        task = asyncio.create_task(fails())
        await asyncio.sleep(0)
        await cancel_and_wait([task])
        del task
        gc.collect()
    finally:
        loop.set_exception_handler(None)
    assert reported == []
