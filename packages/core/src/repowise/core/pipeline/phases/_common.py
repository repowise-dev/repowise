"""Helpers shared across pipeline phases."""

from __future__ import annotations

import contextlib

from repowise.core.pipeline.progress import ProgressCallback


def _phase_done(progress: ProgressCallback | None, phase: str) -> None:
    """Best-effort call to ``progress.on_phase_done`` — older callbacks may
    not implement it, so fall back to a no-op silently.
    """
    if progress is None:
        return
    fn = getattr(progress, "on_phase_done", None)
    if callable(fn):
        with contextlib.suppress(Exception):
            fn(phase)
