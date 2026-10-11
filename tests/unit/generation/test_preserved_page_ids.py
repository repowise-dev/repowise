"""The ids a resumed run reports as deliberately kept.

Regression for issue #1089. ``--resume`` skips pages a prior run already wrote,
so they are absent from ``generated_pages``. Persistence reads that absence as
"this page is gone" and sweeps the structurally-keyed ones, which meant a resume
sent to repair a half-finished wiki deleted the half that had survived. The run
now records every id it skipped for that reason, and only that reason.

Only the first test here fails if the fix is reverted. The other three pin the
negative half of the contract — what must *not* be recorded — which a later
over-broad change would break and nothing else would catch. The end-to-end
version, covering the hand-offs between this gate and persistence, lives in
``tests/integration/test_generation_pipeline.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.generation.page_generator.orchestrate import _GenerationRun


def _run(
    *,
    completed_ids: set[str],
    only_page_ids: set[str] | None = None,
    preserved: set[str] | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        completed_ids=completed_ids,
        only_page_ids=only_page_ids,
        preserved_page_ids=preserved,
    )


def test_resume_skip_is_recorded() -> None:
    preserved: set[str] = set()
    run = _run(completed_ids={"module_page:community-1"}, preserved=preserved)

    assert _GenerationRun._emit(run, "module_page:community-1") is False
    assert preserved == {"module_page:community-1"}


def test_generated_page_is_not_recorded() -> None:
    preserved: set[str] = set()
    run = _run(completed_ids={"module_page:community-1"}, preserved=preserved)

    assert _GenerationRun._emit(run, "module_page:community-2") is True
    assert preserved == set()


def test_scoped_out_page_is_not_recorded() -> None:
    """A scoped run says nothing about the pages it was not asked for.

    ``repowise generate`` skips most of the wiki by design. Recording those as
    preserved would be a claim it never made, and the scoped path does not use
    this sweep anyway (it has ``sweep_superseded_generated_pages``).
    """
    preserved: set[str] = set()
    run = _run(
        completed_ids=set(),
        only_page_ids={"module_page:community-1"},
        preserved=preserved,
    )

    assert _GenerationRun._emit(run, "module_page:community-2") is False
    assert preserved == set()


def test_no_sink_is_tolerated() -> None:
    """Every non-resume caller passes None; the gate must not care."""
    run = _run(completed_ids={"module_page:community-1"}, preserved=None)

    assert _GenerationRun._emit(run, "module_page:community-1") is False
