"""Free and paid page generation must not share one progress bar.

A single bar counting 4,229 items where ~4,134 are template renders and ~95 are
model calls reads as frozen. The cheap levels run first, so it reaches 97% in
minutes and then crawls for another quarter of an hour with the cost figure
sitting still at $0.009 — measured live at 4107/4229, then 4125 three minutes
later. Nothing on screen distinguished that from a hang.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from repowise.core.pipeline.phases.generation import _FREE_PAGE_TYPES, run_generation


class _RecordingProgress:
    def __init__(self) -> None:
        self.started: list[tuple[str, int | None]] = []
        self.items: list[str] = []
        self.done: list[str] = []
        self.costs: list[float] = []

    def on_phase_start(self, phase: str, total: int | None) -> None:
        self.started.append((phase, total))

    def on_item_done(self, phase: str) -> None:
        self.items.append(phase)

    def on_phase_done(self, phase: str) -> None:
        self.done.append(phase)

    def on_message(self, level: str, text: str) -> None:
        pass

    def set_cost(self, total_cost: float) -> None:
        self.costs.append(total_cost)


class _CostTracker:
    session_cost = 0.25


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, page_types: list[str]) -> _RecordingProgress:
    """Drive ``run_generation``'s callbacks with a fixed sequence of page types."""
    import asyncio

    import repowise.core.generation as gen_pkg

    class _FakeGenerator:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def generate_all(self, *a: Any, **kwargs: Any) -> list[Any]:
            on_total_known = kwargs["on_total_known"]
            on_page_done = kwargs["on_page_done"]
            on_total_known(len(page_types))
            for page_type in page_types:
                on_page_done(page_type)
            return []

    monkeypatch.setattr(gen_pkg, "PageGenerator", _FakeGenerator)

    progress = _RecordingProgress()
    asyncio.run(
        run_generation(
            repo_path=tmp_path,
            parsed_files=[],
            source_map={},
            graph_builder=None,
            repo_structure=None,
            git_meta_map={},
            llm_client=None,
            embedder=None,
            vector_store=None,
            concurrency=4,
            progress=progress,
            cost_tracker=_CostTracker(),
        )
    )
    return progress


def test_free_page_types_are_the_ones_that_cost_nothing() -> None:
    """The predicate the split turns on, stated explicitly.

    These render from a Jinja template with no provider call. If a type ever
    moves tier, this is the assertion that should fail first.
    """
    assert {"file_page", "symbol_spotlight", "api_contract", "scc_page"} <= _FREE_PAGE_TYPES
    assert "module_page" not in _FREE_PAGE_TYPES
    assert "repo_overview" not in _FREE_PAGE_TYPES
    assert "onboarding" not in _FREE_PAGE_TYPES


def test_each_tier_advances_only_its_own_bar(monkeypatch, tmp_path: Path) -> None:
    progress = _run(monkeypatch, tmp_path, ["file_page"] * 5 + ["module_page"] * 3)

    assert progress.items.count("generation.llm") == 3
    assert progress.items.count("generation") == 5


def test_a_run_with_no_paid_pages_shows_no_paid_counter(monkeypatch, tmp_path: Path) -> None:
    """``--no-prose`` renders everything from templates. A paid bar stuck at 0
    would be an unanswered question, not an answer."""
    progress = _run(monkeypatch, tmp_path, ["file_page"] * 4)

    assert "generation.llm" not in progress.items


def test_onboarding_counts_as_paid_and_keeps_its_own_step(monkeypatch, tmp_path: Path) -> None:
    """Onboarding slots cost a model call, so they belong to the paid tier.

    Routing them to the free bar would leave that bar unable to reach the
    denominator ``_announce_total`` derives from the same tier split.
    """
    progress = _run(monkeypatch, tmp_path, ["file_page", "module_page", "onboarding", "onboarding"])

    assert progress.items.count("generation") == 1
    assert progress.items.count("generation.llm") == 3
    # Onboarding keeps its own named step as well.
    assert progress.items.count("onboarding") == 2


def test_cost_is_pushed_on_every_page(monkeypatch, tmp_path: Path) -> None:
    """The figure freezing between completions is half of why it looked hung."""
    progress = _run(monkeypatch, tmp_path, ["file_page", "module_page"])

    assert progress.costs == [0.25, 0.25]
