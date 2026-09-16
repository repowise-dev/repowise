"""The pure risk facade must equal the live path, and serve a checkout-less caller.

:func:`assess_change` lets a caller holding only file stats score a change
without a checkout. That is worth nothing unless the live path runs the same
composition, so these tests pin both directions.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.change_risk.features import features_from_file_changes
from repowise.core.analysis.change_risk.model import score_change
from repowise.core.analysis.change_risk.service import (
    _MIN_BASELINE,
    assess_change,
    score_live_change,
)
from tests.unit.change_health.conftest import Repo, python_complex


@pytest.fixture
def make_repo(tmp_path):
    counter = {"n": 0}

    def factory(name: str = "repo") -> Repo:
        counter["n"] += 1
        return Repo(tmp_path / f"{name}{counter['n']}")

    return factory


def _repo_with_history(make_repo, commits: int = 14):
    repo = make_repo("risk")
    for i in range(commits):
        # A mix of shapes so the baseline cohort is not degenerate.
        repo.commit(
            f"fix: adjust {i}" if i % 3 == 0 else f"feat: extend {i}",
            {f"app/m{i % 4}.py": python_complex("run", 2 + (i % 5))},
        )
    return repo


# ---------------------------------------------------------------------------
# Live path delegates to the facade
# ---------------------------------------------------------------------------


def test_live_scoring_equals_the_facade_over_the_same_inputs(make_repo):
    """The live path must be the facade plus IO, not a parallel composition."""
    repo = _repo_with_history(make_repo)
    live = score_live_change(str(repo.path), "HEAD")

    # Re-run the facade over exactly what the live path collected. Every number
    # the result carries has to come back identical.
    replayed = assess_change(
        live.features,
        fix_pressure={} if live.fix_history_available else None,
        baseline_scores=[],
        working_tree=live.working_tree,
        riskignore_excludes=live.riskignore_excludes,
        request_excludes=live.request_excludes,
    )
    assert replayed.risk.score == live.risk.score
    assert replayed.risk.level == live.risk.level
    assert replayed.features == live.features
    assert replayed.fix_history_available == live.fix_history_available


def test_live_scoring_still_ranks_against_its_cohort(make_repo):
    repo = _repo_with_history(make_repo, commits=20)
    live = score_live_change(str(repo.path), "HEAD")
    assert live.baseline_sample_size >= _MIN_BASELINE
    assert live.percentile is not None
    assert live.priority is not None


# ---------------------------------------------------------------------------
# The facade, used the way a checkout-less consumer would
# ---------------------------------------------------------------------------


def _file_stat_features():
    """What a caller without a checkout has: per-file (path, additions, deletions)."""
    return features_from_file_changes(
        [("app/a.py", 40, 10), ("app/b.py", 5, 1), ("docs/readme.md", 2, 0)],
        subject="feat: widen the interface",
        ref="pr-123",
    )


def test_facade_scores_file_stats_with_no_checkout():
    result = assess_change(_file_stat_features())
    assert result.risk.score == score_change(_file_stat_features()).score
    assert result.features.nf == 3
    assert result.features.la == 47
    assert result.features.ld == 11


def test_facade_and_model_agree_on_the_score():
    """No second scorer: the facade's number is the model's number."""
    features = _file_stat_features()
    assert assess_change(features).risk.score == score_change(features).score


# ---------------------------------------------------------------------------
# Explicit states: absent fix history, and too small a cohort
# ---------------------------------------------------------------------------


def test_no_fix_history_is_distinguishable_from_no_fixes():
    features = _file_stat_features()
    could_not_look = assess_change(features, fix_pressure=None)
    looked_and_found_none = assess_change(features, fix_pressure={})

    assert could_not_look.fix_history_available is False
    assert looked_and_found_none.fix_history_available is True
    # Both report zero pressure; only the availability flag tells them apart.
    assert could_not_look.fix_density == looked_and_found_none.fix_density == 0.0


def test_fix_pressure_feeds_density_and_hot_files():
    features = features_from_file_changes([("app/a.py", 30, 0), ("app/b.py", 10, 0)])
    result = assess_change(features, fix_pressure={"app/a.py": 4.0})
    assert result.fix_density > 0
    assert [path for path, _churn, _pressure in result.hot_files] == ["app/a.py"]


def test_too_small_a_cohort_declines_to_rank():
    features = _file_stat_features()
    result = assess_change(features, baseline_scores=[0.1, 0.5, 0.9])
    assert result.baseline_sample_size == 3
    # Not a percentile computed from three samples -- no answer at all.
    assert result.percentile is None
    assert result.priority is None


def test_a_sufficient_cohort_ranks():
    features = _file_stat_features()
    scores = [i / 100 for i in range(1, 41)]
    result = assess_change(features, baseline_scores=scores)
    assert result.baseline_sample_size == 40
    assert result.percentile is not None
    assert result.priority is not None


@pytest.mark.parametrize("size", [_MIN_BASELINE - 1, _MIN_BASELINE])
def test_min_baseline_is_the_boundary(size):
    result = assess_change(_file_stat_features(), baseline_scores=[i / 100 for i in range(size)])
    assert (result.percentile is None) is (size < _MIN_BASELINE)


def test_author_experience_is_excluded_from_the_ranking_score():
    """Experience is a property of the author, not of the change being ranked."""
    scores = [i / 100 for i in range(1, 41)]
    novice = features_from_file_changes([("app/a.py", 40, 10)], exp=0.0)
    veteran = features_from_file_changes([("app/a.py", 40, 10)], exp=500.0)

    ranked_novice = assess_change(novice, baseline_scores=scores)
    ranked_veteran = assess_change(veteran, baseline_scores=scores)

    # The absolute scores differ, because experience is a real model feature...
    assert ranked_novice.risk.score != ranked_veteran.risk.score
    # ...but the repo-relative ranking is about the change, so it does not.
    assert ranked_novice.percentile == ranked_veteran.percentile
    assert ranked_novice.priority == ranked_veteran.priority
