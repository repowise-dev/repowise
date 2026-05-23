"""Pipeline checkpoint/resume integration over the Phase-1 ``JobStore`` seam.

On a large repo a crashed ``repowise init`` should not start from scratch. This
module records each pipeline phase's lifecycle to a :class:`JobStore` so a
re-run can detect which phases already completed and skip them.

Design
------
Phase boundaries are observed through the existing ``pipeline_hooks`` registry
(``HookProgressCallback`` fires ``PRE``/``POST`` hooks around every
``on_phase_start`` / ``on_phase_done``). Those hooks are **synchronous** but
fire inside the running event loop, while ``JobStore`` writes are **async**. To
bridge the two without losing ordering, the hooks push lightweight events onto
an ``asyncio.Queue`` that a single background consumer drains in order —
guaranteeing a phase's ``create_job`` happens before its completion update.

The whole mechanism is **opt-in**: it only activates when a ``JobStore`` is
supplied to :class:`PhaseCheckpointer`. When absent, the orchestrator runs
exactly as before — no new writes, no behaviour change.
"""

from __future__ import annotations

import asyncio
import enum
from typing import TYPE_CHECKING

import structlog

from repowise.core.registry import HookPhase, pipeline_hooks

if TYPE_CHECKING:
    from repowise.core.persistence._interfaces.job_store import JobStore
    from repowise.core.registry import PipelineHookRegistry

logger = structlog.get_logger(__name__)

__all__ = [
    "DEFAULT_CHECKPOINT_PHASES",
    "PhaseCheckpointer",
    "find_completed_phases",
]

# Major phases worth checkpointing. These names match the strings the
# orchestrator passes to ``progress.on_phase_start`` / ``on_phase_done``.
DEFAULT_CHECKPOINT_PHASES: tuple[str, ...] = (
    "parse",
    "git",
    "co_change",
    "generation",
)


class _EventKind(enum.Enum):
    START = "start"
    DONE = "done"
    STOP = "stop"  # sentinel to shut the consumer down


async def find_completed_phases(
    job_store: JobStore, repository_id: str
) -> set[str]:
    """Return the set of phases already COMPLETED for *repository_id*.

    Used by the resume path to skip work that a prior run finished.
    """
    from repowise.core.persistence._interfaces.job_store import JobState

    jobs = await job_store.list_jobs(repository_id=repository_id)
    return {j.phase for j in jobs if j.state == JobState.COMPLETED}


class PhaseCheckpointer:
    """Records phase lifecycle to a ``JobStore``, driven by pipeline hooks.

    Use as an async context manager around a pipeline run::

        async with PhaseCheckpointer(job_store, repo_id) as cp:
            await run_pipeline(..., )

    The consumer task started on ``__aenter__`` drains queued phase events; on
    ``__aexit__`` it is signalled to stop and awaited so all writes land before
    control returns.
    """

    def __init__(
        self,
        job_store: JobStore,
        repository_id: str,
        *,
        phases: tuple[str, ...] = DEFAULT_CHECKPOINT_PHASES,
        registry: PipelineHookRegistry | None = None,
    ) -> None:
        self._job_store = job_store
        self._repository_id = repository_id
        self._phases = phases
        self._registry = registry if registry is not None else pipeline_hooks
        self._queue: asyncio.Queue[tuple[_EventKind, str]] = asyncio.Queue()
        self._job_ids: dict[str, str] = {}
        self._consumer: asyncio.Task[None] | None = None
        self._registered: list[tuple[str, HookPhase, object]] = []

    # -- hook callbacks (sync, fired inside the loop) ----------------------

    def _enqueue_start(self, phase: str) -> None:
        self._queue.put_nowait((_EventKind.START, phase))

    def _enqueue_done(self, phase: str) -> None:
        self._queue.put_nowait((_EventKind.DONE, phase))

    def register(self) -> None:
        """Register pre/post hooks for every tracked phase."""
        for phase in self._phases:
            start_cb = self._make_cb(self._enqueue_start)
            done_cb = self._make_cb(self._enqueue_done)
            self._registry.register(phase, start_cb, when=HookPhase.PRE)
            self._registry.register(phase, done_cb, when=HookPhase.POST)
            self._registered.append((phase, HookPhase.PRE, start_cb))
            self._registered.append((phase, HookPhase.POST, done_cb))

    @staticmethod
    def _make_cb(fn: object):
        # pipeline_hooks expects Callable[[str], None]; bind through a plain
        # function so each registration is a distinct, removable object.
        def _cb(phase: str) -> None:
            fn(phase)  # type: ignore[operator]

        return _cb

    def unregister(self) -> None:
        """Best-effort removal of this checkpointer's hooks from the registry."""
        hooks = getattr(self._registry, "_hooks", None)
        if not isinstance(hooks, dict):
            return
        for phase, when, cb in self._registered:
            bucket = hooks.get((phase, when))
            if bucket and cb in bucket:
                bucket.remove(cb)
        self._registered.clear()

    # -- async consumer ----------------------------------------------------

    async def _consume(self) -> None:
        from repowise.core.persistence._interfaces.job_store import JobState

        while True:
            kind, phase = await self._queue.get()
            try:
                if kind is _EventKind.STOP:
                    return
                if kind is _EventKind.START:
                    job = await self._job_store.create_job(
                        repository_id=self._repository_id, phase=phase
                    )
                    self._job_ids[phase] = job.id
                    await self._job_store.update_state(job.id, JobState.RUNNING)
                elif kind is _EventKind.DONE:
                    job_id = self._job_ids.get(phase)
                    if job_id is not None:
                        await self._job_store.update_state(
                            job_id, JobState.COMPLETED
                        )
            except Exception as exc:  # checkpointing must never break a run
                logger.warning(
                    "checkpoint_write_failed", phase=phase, kind=kind.value, error=str(exc)
                )
            finally:
                self._queue.task_done()

    async def start(self) -> None:
        """Register hooks and start the background consumer."""
        self.register()
        self._consumer = asyncio.create_task(self._consume())

    async def aclose(self) -> None:
        """Drain queued events, stop the consumer, and remove hooks.

        Call on the success path. If the pipeline raises instead, leaving this
        uncalled is intentional: in-flight phases stay in ``RUNNING`` so a
        re-run's ``find_resumable`` can detect the interrupted work.
        """
        await self._queue.join()
        self._queue.put_nowait((_EventKind.STOP, ""))
        if self._consumer is not None:
            await self._consumer
            self._consumer = None
        self.unregister()

    async def __aenter__(self) -> PhaseCheckpointer:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
