"""Ordered, failure-isolated persistence steps.

A writer lists its steps once, in order, and :func:`run_steps` runs them: a
guarded step that raises is reported through :meth:`PersistRun.skip` and the
run carries on. Steps time themselves (``timed``), so each step decides
exactly which ``persist.*`` row it records.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from repowise.core.pipeline.phase_timing import PhaseTimings


def _noop_log(message: str) -> None:  # pragma: no cover - trivial
    return None


@dataclass(kw_only=True)
class PersistRun:
    """What every step of one write shares: where failures and timings go.

    ``degraded`` collects ``"<step>: <error>"`` for every skipped step.
    ``failed_steps`` collects the names of skipped steps marked
    ``range_scoped``: those whose input was this commit range, which the
    caller records as a repair marker. Writers subclass this with their inputs
    and the state their steps pass along.
    """

    log: Callable[[str], None] = _noop_log
    degraded: list[str] | None = None
    failed_steps: list[str] | None = None
    timings: PhaseTimings | None = None

    def skip(self, step: str, exc: Exception, *, range_scoped: bool = False) -> None:
        self.log(f"[yellow]{step} skipped: {exc}[/yellow]")
        if self.degraded is not None:
            self.degraded.append(f"{step}: {exc}")
        if range_scoped and self.failed_steps is not None:
            self.failed_steps.append(step)


@dataclass(frozen=True)
class Step:
    """One persistence step.

    ``name`` is the label a failure is reported under. ``when`` gates the step
    on the run's inputs. ``on_error`` runs before the failure is reported (for
    bookkeeping such as cleanup debt), ``reraise`` decides after it whether the
    failure fails the whole write. An unguarded step runs without a net.
    """

    name: str
    run: Callable[[Any], Awaitable[Any]]
    when: Callable[[Any], bool] | None = None
    range_scoped: bool = False
    on_error: Callable[[Any], None] | None = None
    reraise: Callable[[Any], bool] | None = None
    guarded: bool = True


async def run_step(ctx: PersistRun, step: Step) -> None:
    if step.when is not None and not step.when(ctx):
        return
    if not step.guarded:
        await step.run(ctx)
        return
    try:
        await step.run(ctx)
    except Exception as exc:
        if step.on_error is not None:
            # Bookkeeping about the failure must not replace it: the step is
            # still reported as skipped and the run carries on.
            try:
                step.on_error(ctx)
            except Exception as bookkeeping_exc:
                ctx.log(
                    f"[yellow]{step.name}: could not record the failure: {bookkeeping_exc}[/yellow]"
                )
        ctx.skip(step.name, exc, range_scoped=step.range_scoped)
        if step.reraise is not None and step.reraise(ctx):
            raise


async def run_steps(ctx: PersistRun, steps: Iterable[Step]) -> None:
    """Run *steps* in order against *ctx*."""
    for step in steps:
        await run_step(ctx, step)


__all__ = ["PersistRun", "Step", "run_step", "run_steps"]
