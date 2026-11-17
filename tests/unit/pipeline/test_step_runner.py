"""``run_step``: a failing ``on_error`` must not replace the step's own failure."""

from __future__ import annotations

import pytest

from repowise.core.pipeline.step_runner import PersistRun, Step, run_step


def _ctx(log: list[str]) -> PersistRun:
    return PersistRun(log=log.append, degraded=[], failed_steps=[])


async def _boom(_ctx: PersistRun) -> None:
    raise RuntimeError("boom")


def _ledger_fails(_ctx: PersistRun) -> None:
    raise OSError("ledger")


async def test_failing_on_error_still_skips_the_step() -> None:
    log: list[str] = []
    ctx = _ctx(log)
    step = Step("Cleanup", _boom, range_scoped=True, on_error=_ledger_fails)

    await run_step(ctx, step)

    assert ctx.degraded == ["Cleanup: boom"]
    assert ctx.failed_steps == ["Cleanup"]
    assert any("Cleanup" in line and "ledger" in line for line in log)


async def test_reraise_raises_the_step_error_not_the_bookkeeping_one() -> None:
    ctx = _ctx([])
    step = Step("Cleanup", _boom, on_error=_ledger_fails, reraise=lambda _ctx: True)

    with pytest.raises(RuntimeError, match="boom"):
        await run_step(ctx, step)

    assert ctx.degraded == ["Cleanup: boom"]


async def test_on_error_runs_once_before_skip() -> None:
    calls: list[str] = []

    class _Run(PersistRun):
        def skip(self, step: str, exc: Exception, *, range_scoped: bool = False) -> None:
            calls.append("skip")
            super().skip(step, exc, range_scoped=range_scoped)

    ctx = _Run(degraded=[], failed_steps=[])
    step = Step("Cleanup", _boom, on_error=lambda _ctx: calls.append("on_error"))

    await run_step(ctx, step)

    assert calls == ["on_error", "skip"]
    assert ctx.degraded == ["Cleanup: boom"]
