"""PipelineHookRegistry + HookProgressCallback behavior."""

from __future__ import annotations

import pytest

from repowise.core.registry import (
    HookPhase,
    HookProgressCallback,
    PipelineHookRegistry,
)


@pytest.fixture
def hooks() -> PipelineHookRegistry:
    return PipelineHookRegistry()


def test_register_and_fire_post_hook(hooks):
    seen: list[str] = []
    hooks.register("parse", lambda p: seen.append(f"post:{p}"), when="post")
    hooks.fire("parse", HookPhase.POST)
    assert seen == ["post:parse"]


def test_pre_and_post_separate(hooks):
    seen: list[str] = []
    hooks.register("graph", lambda p: seen.append("pre"), when="pre")
    hooks.register("graph", lambda p: seen.append("post"), when="post")
    hooks.fire("graph", HookPhase.PRE)
    hooks.fire("graph", HookPhase.POST)
    assert seen == ["pre", "post"]


def test_broken_hook_does_not_propagate(hooks):
    def boom(_p: str) -> None:
        raise RuntimeError("plugin bug")

    hooks.register("parse", boom)
    # Must not raise.
    hooks.fire("parse", HookPhase.POST)


def test_unknown_phase_is_silent(hooks):
    # Firing a phase nobody registered against is a no-op.
    hooks.fire("nonexistent", HookPhase.POST)


def test_progress_wrapper_fires_around_inner(hooks):
    class _Inner:
        def __init__(self) -> None:
            self.events: list[str] = []

        def on_phase_start(self, phase: str, total: int | None = None) -> None:
            self.events.append(f"start:{phase}")

        def on_phase_done(self, phase: str) -> None:
            self.events.append(f"done:{phase}")

        def on_item_done(self, phase: str) -> None:
            self.events.append(f"item:{phase}")

    inner = _Inner()
    hook_events: list[str] = []
    hooks.register("parse", lambda p: hook_events.append(f"pre:{p}"), when="pre")
    hooks.register("parse", lambda p: hook_events.append(f"post:{p}"), when="post")

    wrap = HookProgressCallback(inner, hooks)
    wrap.on_phase_start("parse", 3)
    wrap.on_item_done("parse")
    wrap.on_phase_done("parse")

    assert hook_events == ["pre:parse", "post:parse"]
    assert inner.events == ["start:parse", "item:parse", "done:parse"]


def test_progress_wrapper_works_without_inner(hooks):
    hook_events: list[str] = []
    hooks.register("graph", lambda p: hook_events.append(p), when="post")
    wrap = HookProgressCallback(None, hooks)
    # These must not raise.
    wrap.on_phase_start("graph", None)
    wrap.on_item_done("graph")  # forwarded → no-op
    wrap.on_phase_done("graph")
    assert hook_events == ["graph"]


def test_reset_clears_hooks(hooks):
    hooks.register("parse", lambda p: None)
    hooks.reset()
    assert hooks.hooks_for("parse", HookPhase.POST) == []
