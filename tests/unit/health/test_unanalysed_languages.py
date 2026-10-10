"""A file in a language health has no dialect for carries no score.

Nothing walks such a file, so a 10.0 would read as "perfect" when it means
"nobody looked". It is stored unscored and left out of every figure.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.complexity import FileComplexity, walk_file
from repowise.core.analysis.health.complexity.languages import has_health_dialect
from repowise.core.analysis.health.duplication import DuplicationReport
from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.analysis.health.grading import distribution
from repowise.core.analysis.health.models import HealthFileMetricData
from repowise.core.analysis.health.ranking import worst_metric
from repowise.core.analysis.health.scoring import (
    SCORE_FIELDS,
    compute_kpis,
    file_score_fields,
    hotspot_health,
)
from repowise.core.analysis.health.trends import snapshot_fields
from repowise.core.pipeline.incremental import _numbers_moved


@pytest.mark.parametrize("language", ["python", "typescript", "csharp", "razor", "sql", "php"])
def test_languages_health_walks_have_a_dialect(language: str) -> None:
    assert has_health_dialect(language)


@pytest.mark.parametrize("language", ["swift", "elixir", "markdown", "", None])
def test_languages_nothing_walks_have_none(language: str | None) -> None:
    assert not has_health_dialect(language)


def _metric(path: str, score: float | None, nloc: int = 10, **kw) -> HealthFileMetricData:
    return HealthFileMetricData(
        file_path=path,
        score=score,
        max_ccn=None if score is None else 3,
        max_nesting=None if score is None else 1,
        nloc=nloc,
        has_test_file=False,
        defect_score=score,
        **kw,
    )


def _evaluate(path: str, language: str, source: bytes, tmp_path) -> HealthFileMetricData:
    abs_path = tmp_path / path.rsplit("/", 1)[-1]
    abs_path.write_bytes(source)
    fcx = walk_file(str(abs_path), language, source)
    pf = SimpleNamespace(
        file_info=SimpleNamespace(
            path=path, language=language, abs_path=str(abs_path), is_test=False
        ),
        symbols=[],
    )
    metric, _, _ = HealthAnalyzer(graph=None)._evaluate_file(
        pf, fcx, paired_tests=set(), package_roots=set(), disabled=[], dup_report=DuplicationReport()
    )
    return metric


def test_an_unsupported_language_file_is_stored_with_no_score_and_no_complexity(tmp_path) -> None:
    metric = _evaluate(
        "src/Big.ex", "elixir", b"defmodule Big do\ndef f(a), do: a\nend\n", tmp_path
    )
    assert metric.score is None
    assert metric.max_ccn is None and metric.max_nesting is None
    assert all(getattr(metric, name) is None for name in SCORE_FIELDS)
    # What is not a measurement of code shape is still recorded.
    assert metric.nloc > 0


def test_a_python_file_still_scores(tmp_path) -> None:
    """Control: a language with a dialect is scored exactly as before."""
    source = b"def f(a):\n    if a:\n        return 1\n    return 2\n"
    if walk_file("/tmp/x.py", "python", source).file_nloc == 0:
        pytest.skip("python tree-sitter pack missing")
    metric = _evaluate("src/ok.py", "python", source, tmp_path)
    assert metric.score == 10.0
    assert metric.max_ccn == 2


def test_score_fields_are_all_none_without_a_dialect() -> None:
    scores = {"defect": 7.5, "maintainability": 8.0, "performance": 10.0}
    assert file_score_fields(False, scores, []) == dict.fromkeys(SCORE_FIELDS)
    fields = file_score_fields(True, scores, [])
    assert fields["score"] == 7.5 and fields["maintainability_score"] == 8.0


def test_kpis_leave_unscored_files_out() -> None:
    rows = [
        _metric("a.py", 4.0, nloc=100),
        _metric("b.py", 8.0, nloc=100),
        # Huge and unscored: counted as 10.0 it would drag the average to ~9.
        _metric("Big.ex", None, nloc=10_000),
    ]
    kpis = compute_kpis(rows, {"a.py", "Big.ex"})
    assert kpis["average_health"] == 6.0
    assert kpis["hotspot_health"] == 4.0
    assert kpis["file_count"] == 2
    assert kpis["unanalysed_file_count"] == 1
    assert kpis["worst_performer_path"] == "a.py"


def test_a_repository_of_only_unscored_files_has_no_average() -> None:
    kpis = compute_kpis([_metric("A.ex", None), _metric("B.ex", None)], {"A.ex"})
    assert kpis["average_health"] is None
    assert kpis["hotspot_health"] is None
    assert kpis["unanalysed_file_count"] == 2
    # No trend point for a number nobody measured.
    assert snapshot_fields(kpis, [_metric("A.ex", None)], []) is None


def test_an_empty_repository_has_no_average_either() -> None:
    """No rows at all is also no score: a repository of only data and config
    files arrives with no rows, since those files never get one."""
    kpis = compute_kpis([], set())
    assert kpis["average_health"] is None
    assert snapshot_fields(kpis, [], []) is None


def test_unscored_files_are_never_the_worst_or_a_hotspot() -> None:
    rows = [_metric("Big.ex", None), _metric("a.py", 9.0)]
    assert worst_metric(rows, {}).file_path == "a.py"
    assert worst_metric([_metric("Big.ex", None)], {}) is None
    assert hotspot_health(rows, {"Big.ex"}) is None


def test_unscored_files_have_no_band() -> None:
    dist = distribution([_metric("Big.ex", None), _metric("a.py", 9.0)])
    assert sum(band["files"] for band in dist["bands"].values()) == 1


def test_snapshot_score_map_skips_unscored_files() -> None:
    rows = [_metric("Big.ex", None), _metric("a.py", 9.0)]
    fields = snapshot_fields(compute_kpis(rows, set()), rows, [])
    assert fields is not None
    assert fields["per_file_scores"] == {"a.py": 9.0}


def test_an_update_clears_a_stored_ten_for_an_unscored_file() -> None:
    """A store written before this kept 10.0; the history refresh replaces it."""
    stored = SimpleNamespace(score=10.0, structure_deduction=0.0, history_deduction=0.0)
    refreshed = SimpleNamespace(score=None, structure_deduction=None, history_deduction=None)
    assert _numbers_moved(refreshed, stored)
    stored_none = SimpleNamespace(score=None, structure_deduction=None, history_deduction=None)
    assert not _numbers_moved(refreshed, stored_none)


def test_walk_of_an_unsupported_language_is_empty() -> None:
    """Why the score is meaningless: no function is ever measured."""
    fcx = walk_file("/tmp/x.ex", "elixir", b"defmodule X do\ndef f, do: 1\nend\n")
    assert isinstance(fcx, FileComplexity)
    assert fcx.functions == []


def test_get_health_names_an_unscored_target_for_what_it_is(tmp_path) -> None:
    from repowise.server.mcp_server.tool_health.targets import _unresolved_targets

    (tmp_path / "Big.ex").write_text("defmodule Big do\nend\n")
    out = _unresolved_targets(
        file_targets=["Big.ex"],
        module_targets=[],
        matched_modules=set(),
        resolved_paths=set(),
        excluded_paths=set(),
        unscored_paths=set(),
        repo_root=tmp_path,
        unanalysed_paths={"Big.ex"},
    )
    assert out == [{"target": "Big.ex", "reason": "language_not_supported"}]


def _refresh(language: str | None, stored_score: float | None):
    from repowise.core.analysis.health.history_refresh import refresh_history

    metric = _metric("src/a.py", stored_score)
    languages = {} if language is None else {"src/a.py": language}
    (out,) = refresh_history(
        metrics=[metric],
        findings_by_path={},
        git_meta_by_path={"src/a.py": {"commit_count_90d": 1}},
        languages=languages,
    )
    return out


def test_the_history_refresh_nulls_a_language_with_no_dialect() -> None:
    assert _refresh("elixir", 10.0).score is None


def test_a_file_with_no_graph_node_keeps_its_score() -> None:
    """A missing language is not evidence the file was never walked."""
    assert _refresh(None, 7.5).score is not None
    assert _refresh(None, None).score is None
    assert _refresh("python", 7.5).score is not None
