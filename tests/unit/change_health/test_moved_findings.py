"""Findings that travel with moved code, and findings whose metric dropped."""

from __future__ import annotations

from types import MappingProxyType

from repowise.core.analysis.change_health.matcher import FindingMatcher
from repowise.core.analysis.change_health.moves import MoveMap
from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest
from repowise.core.analysis.health import HealthFindingData, Severity

from .conftest import python_complex


def finding(path, symbol, *, severity=Severity.HIGH, ccn=20, line=10):
    return HealthFindingData(
        biomarker_type="complex_method",
        severity=severity,
        file_path=path,
        function_name=symbol,
        line_start=line,
        line_end=line + 30,
        details={"ccn": ccn},
        health_impact=1.0,
        reason=f"{symbol} has cyclomatic complexity {ccn}",
        dimension="maintainability",
    )


KNOT_MOVED = MoveMap(MappingProxyType({("pkg/knot.py", "knot"): ("tool.py", "knot")}))


def compare(repo):
    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    return service.compare(DeltaRequest(str(repo.path), "HEAD"))


# -- matcher -----------------------------------------------------------------


def test_a_finding_that_moved_with_its_code_pairs_with_its_origin():
    base = [finding("tool.py", "knot", line=200)]
    head = [finding("pkg/knot.py", "knot", line=1)]

    result = FindingMatcher(moves=KNOT_MOVED).match(base, head)

    assert [m.kind for m in result.matched] == ["moved"]
    assert result.matched[0].moved_from == ("tool.py", "knot")
    assert result.resolved == []
    assert result.moved_total == 1


def test_a_moved_finding_that_measures_worse_is_worsened():
    base = [finding("tool.py", "knot", severity=Severity.MEDIUM)]
    head = [finding("pkg/knot.py", "knot", severity=Severity.CRITICAL, ccn=60)]

    result = FindingMatcher(moves=KNOT_MOVED).match(base, head)

    assert [m.kind for m in result.matched] == ["worsened"]
    assert result.moved_total == 1


def test_a_moved_symbol_only_claims_its_own_marker():
    base = [finding("tool.py", "knot")]
    other = finding("pkg/knot.py", "knot")
    other.biomarker_type = "deep_nesting"

    result = FindingMatcher(moves=KNOT_MOVED).match(base, [other])

    assert [m.kind for m in result.matched] == ["introduced"]
    assert len(result.resolved) == 1


def test_a_shrinking_function_with_a_lower_metric_is_improved_not_new():
    base = [finding("app.py", "init_command", severity=Severity.CRITICAL, ccn=163)]
    head = [finding("app.py", "init_command", severity=Severity.CRITICAL, ccn=74, line=40)]
    head[0].health_impact = 0.4

    result = FindingMatcher().match(base, head)

    assert [m.kind for m in result.matched] == ["improved"]
    assert result.of_kind("introduced", "worsened") == []


def test_a_lower_severity_on_the_same_symbol_is_improved():
    base = [finding("app.py", "run_update", severity=Severity.CRITICAL, ccn=282)]
    head = [finding("app.py", "run_update", severity=Severity.HIGH, ccn=30)]

    result = FindingMatcher().match(base, head)

    assert [m.kind for m in result.matched] == ["improved"]
    assert result.improved_total == 1


# -- end to end --------------------------------------------------------------


def test_a_partial_split_reports_the_moved_findings_as_moved(make_repo):
    repo = make_repo()
    repo.commit("seed", {"tool.py": python_complex("tangle", 14) + python_complex("knot", 15)})
    repo.commit(
        "split", {"tool.py": python_complex("tangle", 14), "knots.py": python_complex("knot", 15)}
    )

    delta = compare(repo)

    assert delta.introduced_total == 0
    assert delta.resolved_total == 0
    assert delta.moved_total >= 1


def test_a_moved_function_that_grew_surfaces_as_worsened_with_its_origin(make_repo):
    repo = make_repo()
    repo.commit("seed", {"tool.py": python_complex("tangle", 14) + python_complex("knot", 5)})
    repo.commit(
        "split", {"tool.py": python_complex("tangle", 14), "knots.py": python_complex("knot", 9)}
    )

    delta = compare(repo)

    knots = [f for f in delta.findings if f.path == "knots.py"]
    assert knots and all(f.change_kind == "worsened" for f in knots)
    assert {f.attribution_basis for f in knots} == {"moved"}
    assert knots[0].moved_from == {"path": "tool.py", "symbol": "knot"}
    assert delta.introduced_total == 0


def test_a_function_that_shrank_in_place_counts_as_improved(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": python_complex("tangle", 40)})
    repo.commit("shrink", {"app.py": python_complex("tangle", 16)})

    delta = compare(repo)

    assert delta.introduced_total == 0
    assert delta.worsened_total == 0
    assert delta.improved_total >= 1


def test_two_markers_on_one_origin_pair_by_what_they_measured():
    base = [
        finding("tool.py", "knot", ccn=20, line=10),
        finding("tool.py", "knot", ccn=30, line=90),
    ]
    head = [finding("pkg/knot.py", "knot", ccn=30, line=5), finding("pkg/knot.py", "knot", ccn=20)]

    result = FindingMatcher(moves=KNOT_MOVED).match(base, head)

    pairs = {m.head.details["ccn"]: m.base.details["ccn"] for m in result.matched}
    assert pairs == {20: 20, 30: 30}
    assert [m.kind for m in result.matched] == ["moved", "moved"]


def test_a_nesting_drop_within_one_band_is_improved():
    base, head = finding("app.py", "walk"), finding("app.py", "walk")
    base.biomarker_type = head.biomarker_type = "deep_nesting"
    base.details, head.details = {"max_nesting": 6}, {"max_nesting": 5}

    assert [m.kind for m in FindingMatcher().match([base], [head]).matched] == ["improved"]


def test_the_mcp_block_names_moved_findings_and_stays_lean_without_them(make_repo):
    from repowise.server.mcp_server._change_health import health_delta_block

    repo = make_repo()
    repo.commit("seed", {"tool.py": python_complex("tangle", 14) + python_complex("knot", 5)})
    repo.commit(
        "split", {"tool.py": python_complex("tangle", 14), "knots.py": python_complex("knot", 9)}
    )
    block = health_delta_block(compare(repo), revspec="HEAD")
    assert block["moved"] >= 1
    assert block["top_findings"][0]["moved_from"] == {"path": "tool.py", "symbol": "knot"}

    repo.commit("touch", {"tool.py": python_complex("tangle", 14) + "\n"})
    assert {"moved", "improved"}.isdisjoint(health_delta_block(compare(repo), revspec="HEAD"))
