"""The progress total announced by ``_GenerationRun._announce_total``.

Regression for issue #922: the up-front total omitted the level-8 onboarding
pages (the non-promoted slots), so the bar could report more pages completed
than its total (e.g. "43 of 41"). The estimate must now include one slot per
registered onboarding spec that has not already been generated, gated by
``config.enable_onboarding``.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.generation import onboarding as _onboarding
from repowise.core.generation.models import compute_page_id
from repowise.core.generation.page_generator.orchestrate import _GenerationRun


def _selection() -> SimpleNamespace:
    """A selection with a single file page and nothing else, so the onboarding
    contribution is the only moving part of the total."""
    return SimpleNamespace(
        counts=lambda: {
            "api_contract": 0,
            "symbol_spotlight": 0,
            "file_page": 1,
            "scc_page": 0,
            "module_page": 0,
            "infra_page": 0,
        },
        emit_repo_overview=False,
        emit_arch_diagram=False,
        deterministic_tail_paths=[],
    )


def _fake_run(*, enable_onboarding: bool, completed_ids: set[str]) -> SimpleNamespace:
    announced: list[int] = []
    return SimpleNamespace(
        selection=_selection(),
        kg_ctx=SimpleNamespace(available=False),
        config=SimpleNamespace(enable_onboarding=enable_onboarding),
        completed_ids=completed_ids,
        on_total_known=announced.append,
        job_system=None,
        job_id=None,
        _announced=announced,
    )


def test_total_includes_onboarding_pages() -> None:
    specs = _onboarding.iter_specs()
    assert specs, "expected onboarding subkinds to be registered on import"

    run = _fake_run(enable_onboarding=True, completed_ids=set())
    _GenerationRun._announce_total(run)

    (total,) = run._announced
    # 1 file page + one page per registered onboarding slot.
    assert total == 1 + len(specs)


def test_onboarding_disabled_excludes_those_pages() -> None:
    run = _fake_run(enable_onboarding=False, completed_ids=set())
    _GenerationRun._announce_total(run)

    (total,) = run._announced
    assert total == 1


def test_already_generated_onboarding_pages_are_not_recounted() -> None:
    specs = _onboarding.iter_specs()
    done = {compute_page_id("onboarding", _onboarding.target_path(specs[0].slot))}
    run = _fake_run(enable_onboarding=True, completed_ids=done)
    _GenerationRun._announce_total(run)

    (total,) = run._announced
    # The one already-completed slot drops out of the remaining total.
    assert total == 1 + len(specs) - 1
