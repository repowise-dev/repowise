"""Coverage and tests can never be what a health comparison charges to a change.

``repowise health check`` analyses both sides from git with no index, so no
stored coverage reaches either side. ``repowise health --score --against``
scores both sides with the same index inputs, so stored coverage and test
pairing move both sides together. Either way, deleting tests or changing the
stored coverage makes no finding introduced or worsened.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.health_cmd import live_score
from repowise.core.analysis.change_health.analyzer import RevisionHealthAnalyzer
from repowise.core.analysis.change_health.index_inputs import IndexInputs
from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest

from .conftest import python_complex

_SOURCE = "app/hot.py"
_TEST = "tests/test_hot.py"
_COVERAGE_KINDS = {"untested_hotspot", "coverage_gap", "coverage_gradient"}
_TEST_BODY = "from app.hot import run\n\n\ndef test_run():\n    assert run(4) is None\n"


def _seed_then_drop_the_test(make_repo):
    repo = make_repo()
    repo.commit("seed", {_SOURCE: python_complex("run", 6), _TEST: _TEST_BODY})
    repo.remove(_TEST)
    # The source changes too, so the comparison has a code file to score.
    repo.commit("drop the test", {_SOURCE: python_complex("run", 6) + "\nVALUE = 1\n"})
    return repo


def test_health_check_reads_no_stored_coverage_so_dropping_a_test_charges_nothing(
    make_repo, monkeypatch
):
    def _no_store(*_args, **_kwargs):
        raise AssertionError("health check must not read stored coverage")

    monkeypatch.setattr("repowise.core.persistence.crud.load_coverage_map", _no_store)
    indexes: list = []
    real_analyze = RevisionHealthAnalyzer.analyze

    def _spy(self, sources, **kwargs):
        indexes.append(kwargs.get("index"))
        return real_analyze(self, sources, **kwargs)

    monkeypatch.setattr(RevisionHealthAnalyzer, "analyze", _spy)
    repo = _seed_then_drop_the_test(make_repo)

    delta = ChangeHealthDeltaService(repo_path=str(repo.path)).compare(
        DeltaRequest(str(repo.path), "HEAD")
    )

    # Both sides were analysed, and neither with the index's coverage or pairing.
    assert delta.scope.analyzed >= 1
    assert len(indexes) == 2 and indexes == [None, None]
    charged = [
        f
        for f in delta.findings
        if f.change_kind in {"introduced", "worsened"} and f.biomarker_type in _COVERAGE_KINDS
    ]
    assert charged == []


def _index(covered: list[int], *, tested: bool) -> IndexInputs:
    return IndexInputs(
        git_meta={_SOURCE: {"file_path": _SOURCE, "is_hotspot": True}},
        coverage={
            _SOURCE: {
                "line_coverage_pct": 100.0 * len(covered) / 60,
                "branch_coverage_pct": None,
                "covered_lines": covered,
                "total_coverable_lines": 60,
                "source_format": "lcov",
            }
        },
        tested_files=frozenset({_SOURCE}) if tested else frozenset(),
        dependents={_SOURCE: 12},
    )


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        # Nothing measured, nothing paired: the hotspot reads untested on both sides.
        (_index([], tested=False), {"untested_hotspot"}),
        # Fully covered and paired: no coverage finding on either side.
        (_index(list(range(1, 61)), tested=True), set()),
    ],
    ids=["uncovered", "covered"],
)
def test_live_score_against_gives_both_sides_the_same_stored_coverage(
    make_repo, monkeypatch, inputs, expected
):
    repo = make_repo()
    repo.commit("seed", {_SOURCE: python_complex("run", 6), _TEST: _TEST_BODY})
    repo.remove(_TEST)
    repo.commit("drop the test")
    monkeypatch.setattr(live_score, "_index_inputs", lambda _path: inputs)

    scores = live_score.score_live(Path(repo.path), [_SOURCE], "HEAD~1")

    row = next(r for r in scores.rows if r.path == _SOURCE)
    head_kinds = {f.biomarker_type for f in scores.head.findings} & _COVERAGE_KINDS
    assert expected <= head_kinds
    # Dropping the test and the stored coverage reach both sides alike, so the
    # unchanged file scores the same at HEAD~1 and now.
    assert row.base_score is not None
    assert row.head_score == row.base_score
