"""The Review page as Markdown for an agent: same facts, fixed order, bounded size."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from repowise.core.analysis.risk_semantics import structural_impact_contract
from repowise.server.review_markdown import (
    CHANGED_LIMIT,
    DEPENDENTS_LIMIT,
    TESTS_LIMIT,
    render_change_markdown,
)
from repowise.server.schemas.blast_radius import (
    ChangeBlastRadiusResponse,
    ChangeReview,
    ChangeRiskBlock,
    ChangeTestsBlock,
)
from repowise.server.schemas.git import RiskDriverResponse
from tests.unit.server.conftest import create_test_repo
from tests.unit.server.test_blast_radius_changes import _git_repo

_GOLDEN = Path(__file__).parent / "golden" / "change_review.md"


def _recommendation(test: str, basis: str = "inferred") -> dict[str, Any]:
    return {
        "test_id": test,
        "test_file": test,
        "repository_id": "r1",
        "repository": "demo",
        "basis": basis,
        "bases": [basis],
        "source_files": ["src/core.py"],
        "evidence": [],
    }


def _response(**over: Any) -> ChangeBlastRadiusResponse:
    recs = [_recommendation("tests/test_core.py", "measured"), _recommendation("tests/test_api.py")]
    body: dict[str, Any] = {
        "change_id": "branch:feat/parser",
        "changed_files": ["src/core.py", "src/util.py", "src/__init__.py"],
        "changed_files_total": 3,
        "affected_signals": [
            {"path": "src/core.py", "is_hotspot": True, "health_score": 4.2, "module": "src", "indexed": True},
            {"path": "src/util.py", "is_hotspot": False, "health_score": 8.9, "module": "src", "indexed": True},
            {"path": "src/api.py", "is_hotspot": False, "health_score": 6.1, "module": "src", "indexed": True},
            {"path": "app/main.py", "is_hotspot": True, "health_score": None, "module": "app", "indexed": True},
            {"path": "tests/test_api.py", "is_test": True, "indexed": True},
        ],
        "changed_owners": [{"email": "ann@example.com", "name": "Ann Lee", "files": 2}],
        "test_commands": ["pytest tests/test_core.py tests/test_api.py"],
        "direct_risks": [],
        "transitive_affected": [
            {"path": "src/api.py", "depth": 1, "via": "src/core.py"},
            {"path": "tests/test_api.py", "depth": 1, "via": "src/core.py"},
            {"path": "app/main.py", "depth": 2, "via": "src/api.py"},
        ],
        "cochange_warnings": [
            {"changed": "src/core.py", "missing_partner": "docs/core.md", "score": 0.8},
            {"changed": "src/util.py", "missing_partner": "docs/core.md", "score": 0.5},
        ],
        "recommended_reviewers": [{"email": "bo@example.com", "files": 1, "ownership_pct": 0.5}],
        "test_gaps": ["src/util.py", "src/__init__.py"],
        "test_impact": {
            "recommendations": recs,
            "recommendations_total": 2,
            "recommendations_emitted": 2,
            "recommendations_truncated": False,
            "recommendations_omitted": 0,
            "recommendations_by_primary_basis": {"measured": 1, "inferred": 1},
            "files": [],
            "files_total": 3,
            "files_without_measured_tests": [],
            "unknown_files": [],
            "coverage": {
                "status": "partial",
                "map_present": True,
                "pair_count": 4,
                "test_count": 2,
                "source_file_count": 2,
                "changed_files_total": 3,
                "changed_files_with_measured_tests": 1,
                "changed_files_without_measured_tests": 2,
                "freshness": {"status": "current"},
            },
            "inference": {
                "status": "available",
                "changed_files_total": 3,
                "changed_files_with_candidates": 2,
                "candidates_before_dedup": 2,
            },
            "analysis": {
                "status": "partial",
                "stale": False,
                "partial": True,
                "degraded": False,
                "basis_categories": ["measured", "inferred"],
            },
        },
        **structural_impact_contract(4.2),
    }
    body.update(over)
    return ChangeBlastRadiusResponse.model_validate(body)


def _review(**tests: Any) -> ChangeReview:
    # Only the fields the Markdown reads; the rest of the risk block is the CLI's JSON.
    risk = ChangeRiskBlock.model_construct(
        review_priority="high",
        risk_percentile=91.0,
        score=6.4,
        score_measures="how big and spread out the diff is",
        drivers=[
            RiskDriverResponse(feature="la", value=420, contribution=0.81, label="Lines added"),
            RiskDriverResponse(feature="exp", value=3, contribution=-0.12, label="Author experience"),
        ],
    )
    block = {
        "status": "selected",
        "run_all": False,
        "reasons": [],
        "basis": "measured",
        "tests_to_run": ["tests/test_core.py"],
        "total": 1,
        "truncated": False,
        "summary": "1 test file to run, chosen by coverage.",
        **tests,
    }
    return ChangeReview.model_construct(
        revspec="main...feat/parser", risk=risk, tests=ChangeTestsBlock(**block)
    )


def test_renders_the_page_as_markdown_snapshot() -> None:
    text = render_change_markdown(_response(review=_review()))
    if os.environ.get("REPOWISE_REWRITE_GOLDEN") == "1":
        _GOLDEN.write_text(text, encoding="utf-8", newline="\n")
        pytest.skip("golden rewritten")
    assert text == _GOLDEN.read_text(encoding="utf-8")


def test_a_run_all_selection_says_why_and_an_unscored_change_says_why() -> None:
    reasons = [f"reason {i}" for i in range(8)]
    text = render_change_markdown(
        _response(review=_review(run_all=True, reasons=reasons, tests_to_run=[], total=0, summary="Run everything."))
    )
    assert "Run the whole suite:\n- reason 0" in text
    assert "And 3 more reasons." in text
    assert "## Score and tests" in text and text.index("## Score and tests") < text.index("## Look at first")

    unscored = render_change_markdown(_response(review_unavailable="This server has no checkout."))
    assert "## Score and tests\n\nNot scored: This server has no checkout." in unscored
    assert "## Score and tests" not in render_change_markdown(_response())


def test_rendering_is_deterministic() -> None:
    assert render_change_markdown(_response()) == render_change_markdown(_response())


def test_a_health_check_that_could_not_run_says_why() -> None:
    text = render_change_markdown(_response(), "unknown branch 'gone'")
    assert "## Health check\n\nThe health check could not run: unknown branch 'gone'" in text
    # It sits after the page's actions and before the evidence, as on the page.
    assert text.index("## Usually change with these") < text.index("## Health check")
    assert text.index("## Health check") < text.index("## Files that depend on this")
    assert "## Health check" not in render_change_markdown(_response())


def test_every_list_is_capped_and_says_how_much_it_left_out() -> None:
    many = 500
    changed = [f"src/m{i:03}.py" for i in range(many)]
    reach = [{"path": f"lib/d{i:03}.py", "depth": 1, "via": "src/m000.py"} for i in range(many)]
    tests = [_recommendation(f"tests/test_{i:03}.py") for i in range(60)]
    big = _response(
        changed_files=changed,
        changed_files_total=many + 100,
        affected_signals=[],
        transitive_affected=reach,
        test_gaps=[],
        test_commands=["pytest " + " ".join(f"tests/test_{i:05}.py" for i in range(2000))],
        test_impact={**_response().test_impact.model_dump(), "recommendations": tests, "recommendations_total": 90},
    )
    text = render_change_markdown(big)

    assert len(text.encode()) < 20_000
    assert f"And {many - CHANGED_LIMIT} more files." in text
    assert "100 more changed files not analyzed." in text
    assert f"And {many - DEPENDENTS_LIMIT} more files." in text
    # The listed tests are capped, and the count covers the ones the analysis never listed.
    assert f"And {90 - TESTS_LIMIT} more tests." in text
    # A command too long to include is left out whole, never cut.
    assert "too long to include" in text
    assert "tests/test_01999.py" not in text


async def test_route_serves_markdown_for_a_listed_change(client, tmp_path) -> None:
    _git_repo(tmp_path)
    repo = await create_test_repo(client, tmp_path)
    url = f"/api/repos/{repo['id']}/blast-radius/change.md"

    resp = await client.get(url, params={"change_id": "branch:feat/x"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/markdown")
    assert resp.text.startswith("# Change review: Branch `feat/x`\n")
    assert "- Changed: 1 file" in resp.text
    assert "## Health check" not in resp.text

    checked = await client.get(url, params={"change_id": "branch:feat/x", "health_check": "true"})
    assert checked.status_code == 200, checked.text
    assert "## Health check\n\n**Health:" in checked.text

    assert (await client.get(url, params={"change_id": "branch:gone"})).status_code == 404
