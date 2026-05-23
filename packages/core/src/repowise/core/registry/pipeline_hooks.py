"""Pipeline hooks — observe pipeline phase transitions from a plugin.

The orchestrator already announces every phase transition by calling
``progress.on_phase_start(phase)`` and ``progress.on_phase_done(phase)``
on the active :class:`ProgressCallback`. That single chokepoint is the
natural place to fire third-party callbacks: a hook registered against
phase ``parse`` runs around the parse phase no matter which CLI / Modal /
test entry point launched the pipeline.

:class:`HookProgressCallback` wraps an existing :class:`ProgressCallback`
(or ``None``) and fires registered hooks around each transition while
forwarding every other method to the inner callback unchanged. The
orchestrator only needs to wrap its incoming progress callback in
``HookProgressCallback(progress, pipeline_hooks)`` — a zero-op when no
hooks are registered.

Usage::

    from repowise.core.registry import pipeline_hooks, register_hook

    def warm_caches(phase: str) -> None:
        ...

    register_hook("graph", warm_caches, when="pre")
    register_hook("graph", lambda phase: ..., when="post")

Hook callbacks accept the phase name as their only positional argument.
Exceptions raised inside a hook are caught and logged so a broken plugin
cannot derail the pipeline.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from typing import Any

import structlog

log = structlog.get_logger(__name__)

HookCallback = Callable[[str], None]


class HookPhase(enum.StrEnum):
    """When a hook should fire relative to the wrapped phase."""

    PRE = "pre"
    POST = "post"


class PipelineHookRegistry:
    """Collects callbacks keyed on ``(phase, HookPhase)``."""

    def __init__(self) -> None:
        self._hooks: dict[tuple[str, HookPhase], list[HookCallback]] = {}

    def register(
        self,
        phase: str,
        callback: HookCallback,
        *,
        when: HookPhase | str = HookPhase.POST,
    ) -> None:
        """Register *callback* to fire ``pre`` or ``post`` *phase*."""
        when_enum = when if isinstance(when, HookPhase) else HookPhase(when)
        self._hooks.setdefault((phase, when_enum), []).append(callback)

    def fire(self, phase: str, when: HookPhase) -> None:
        """Invoke every registered hook for ``(phase, when)``.

        Exceptions from individual callbacks are logged and swallowed —
        one broken plugin must not derail the pipeline.
        """
        for cb in self._hooks.get((phase, when), ()):
            try:
                cb(phase)
            except Exception as exc:  # pragma: no cover - safety net
                log.warning(
                    "pipeline_hook_failed",
                    phase=phase,
                    when=when.value,
                    callback=getattr(cb, "__qualname__", repr(cb)),
                    error=str(exc),
                )

    def reset(self) -> None:
        """Drop every registered hook. Used by tests."""
        self._hooks.clear()

    def hooks_for(self, phase: str, when: HookPhase) -> list[HookCallback]:
        """Return registered hooks for ``(phase, when)``. Used by tests."""
        return list(self._hooks.get((phase, when), ()))


pipeline_hooks = PipelineHookRegistry()
"""Process-wide default registry used by the orchestrator."""


def register_hook(
    phase: str,
    callback: HookCallback,
    *,
    when: HookPhase | str = HookPhase.POST,
) -> None:
    """Module-level convenience over :meth:`PipelineHookRegistry.register`."""
    pipeline_hooks.register(phase, callback, when=when)


class HookProgressCallback:
    """Wraps a :class:`ProgressCallback`, firing hooks around transitions.

    Forwards :meth:`on_phase_start` and :meth:`on_phase_done` to the
    inner callback (when one is supplied) and fires the registered
    pre/post hooks around them. ``on_item_done`` and ``on_message`` (plus
    any other attributes a custom callback exposes) are forwarded as-is.
    """

    def __init__(
        self,
        inner: Any | None,
        registry: PipelineHookRegistry | None = None,
    ) -> None:
        self._inner = inner
        self._registry = registry if registry is not None else pipeline_hooks

    def on_phase_start(self, phase: str, total: int | None = None) -> None:
        self._registry.fire(phase, HookPhase.PRE)
        if self._inner is not None:
            self._inner.on_phase_start(phase, total)

    def on_phase_done(self, phase: str) -> None:
        if self._inner is not None:
            done = getattr(self._inner, "on_phase_done", None)
            if done is not None:
                done(phase)
        self._registry.fire(phase, HookPhase.POST)

    def __getattr__(self, name: str) -> Any:
        # Forward anything else (on_item_done, on_message, custom attrs)
        # to the wrapped callback so existing implementations keep working.
        # When no inner callback was supplied, return a no-op for the well-
        # known ProgressCallback methods so the orchestrator can treat the
        # wrapper as a real callback unconditionally.
        if self._inner is None:
            if name.startswith("on_"):
                return _noop_method
            raise AttributeError(name)
        return getattr(self._inner, name)


def _noop_method(*_args: Any, **_kwargs: Any) -> None:
    """Default no-op used when ``HookProgressCallback`` wraps a ``None``
    inner callback and code calls a forwarded method on it."""


__all__ = [
    "HookCallback",
    "HookPhase",
    "HookProgressCallback",
    "PipelineHookRegistry",
    "pipeline_hooks",
    "register_hook",
]
