"""The agent-facing contract: what leads, what is opt-in, what survives pressure."""

from __future__ import annotations

import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest

from .conftest import python_complex, python_io_in_loop

MODULE = "repowise.server.mcp_server.tool_change_risk"


@pytest.fixture
def tool(monkeypatch):
    """The MCP tool, bound to a throwaway repo and with no index behind it."""
    module = importlib.import_module(MODULE)
    module._DELTA_SERVICES.clear()

    def bind(repo):
        async def _context(_alias=None):
            return SimpleNamespace(path=str(repo.path), alias=None)

        monkeypatch.setattr(module, "_resolve_repo_context", _context)
        return module

    return bind


def seeded(make_repo, *, branches: int = 20):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit("add", {"app.py": python_complex("tangle", branches)})
    return repo


# -- ordering and size ------------------------------------------------------


async def test_the_response_leads_with_the_directive_then_the_delta(tool, make_repo):
    module = tool(seeded(make_repo))

    result = await module.get_change_risk("HEAD", baseline=0)

    assert list(result)[:2] == ["directive", "health_delta"]
    assert result["directive"]["headline"]
    assert result["directive"]["next_actions"]


async def test_the_default_response_stays_small_and_omits_score_mechanics(tool, make_repo):
    module = tool(seeded(make_repo))

    result = await module.get_change_risk("HEAD", baseline=0)

    assert len(json.dumps(result, default=str)) < 8000
    for field in ("drivers", "risk_authority", "features", "score_measures", "score_unit"):
        assert field not in result
    assert result["change_shape"]["diagnostics_via"] == ("get_change_risk(include=['diagnostics'])")


async def test_diagnostics_are_a_projection_not_a_removal(tool, make_repo):
    module = tool(seeded(make_repo))

    expanded = await module.get_change_risk("HEAD", baseline=0, include=["diagnostics"])

    for field in ("drivers", "risk_authority", "features", "score_measures"):
        assert field in expanded


async def test_legacy_ranked_fields_stay_at_the_top_level(tool, make_repo):
    """The chat summary and artifact renderer read these by name."""
    module = tool(seeded(make_repo))

    result = await module.get_change_risk("HEAD", baseline=0)

    for field in ("ref", "score", "risk_percentile", "review_priority", "classification"):
        assert field in result


async def test_the_top_findings_cap_is_recoverable(tool, make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit(
        "add",
        {"app.py": "".join(python_complex(f"tangle{i}", 18) for i in range(6))},
    )
    module = tool(repo)

    result = await module.get_change_risk("HEAD", baseline=0)
    delta = result["health_delta"]

    assert delta["findings_emitted"] <= 3
    if delta["findings_total"] > delta["findings_emitted"]:
        assert delta["findings_reduced_reason"] == "top_findings_cap"
        full = await module.get_change_risk("HEAD", baseline=0, include=["findings"])
        assert full["health_delta"]["findings_emitted"] == delta["findings_total"]


# -- drill-down -------------------------------------------------------------


async def test_a_surfaced_finding_can_be_expanded_by_its_id(tool, make_repo):
    module = tool(seeded(make_repo))

    listing = await module.get_change_risk("HEAD", baseline=0)
    row = listing["health_delta"]["top_findings"][0]
    assert row["inspect"] == f"get_change_risk(revspec='HEAD', finding_id={row['id']!r})"

    detail = await module.get_change_risk("HEAD", baseline=0, finding_id=row["id"])

    assert detail["finding"]["id"] == row["id"]
    assert "evidence" in detail["finding"]


async def test_an_unknown_finding_id_says_so_and_lists_the_real_ones(tool, make_repo):
    module = tool(seeded(make_repo))

    result = await module.get_change_risk("HEAD", baseline=0, finding_id="chf_missing")

    assert "error" in result
    assert result["available"]


# -- honesty at the surface -------------------------------------------------


async def test_a_clean_change_says_analyzed_scope_not_safe(tool, make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit("edit", {"app.py": "x = 2\n"})
    module = tool(repo)

    result = await module.get_change_risk("HEAD", baseline=0)

    assert result["directive"]["status"] == "clear_in_analyzed_scope"
    assert "analyzed scope" in result["health_delta"]["explanation"]
    assert "safe" not in result["directive"]["headline"].lower()


async def test_a_partial_comparison_never_reads_as_clear(tool, make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    repo.commit("mixed", {"app.py": python_complex("tangle", 18), "notes.md": "text\n"})
    module = tool(repo)

    result = await module.get_change_risk("HEAD", baseline=0)

    assert result["health_delta"]["status"] == "partial"
    assert result["health_delta"]["skipped"]["total"] == 1
    assert any("not analysed" in reason for reason in result["directive"]["reasons"])


async def test_performance_findings_do_not_by_themselves_demand_review(tool, make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": python_io_in_loop(in_loop=False)})
    repo.commit("loop", {"app.py": python_io_in_loop(in_loop=True)})
    module = tool(repo)

    result = await module.get_change_risk("HEAD", baseline=0)
    delta = result["health_delta"]

    assert delta["by_dimension"].get("performance")
    assert result["directive"]["status"] == "review_recommended"
    row = next(f for f in delta["top_findings"] if f["dimension"] == "performance")
    assert row["opportunity_id"]
    assert row["opportunity_rank"] is not None


async def test_every_surfaced_finding_carries_an_attribution(tool, make_repo):
    module = tool(seeded(make_repo))

    result = await module.get_change_risk("HEAD", baseline=0)

    for row in result["health_delta"]["top_findings"]:
        assert row["attribution"]["basis"]
        assert row["attribution"]["confidence"] in {"high", "medium", "low"}
        assert row["attribution"]["why"]


async def test_the_delta_names_the_analyzer_and_the_two_sides(tool, make_repo):
    module = tool(seeded(make_repo))

    delta = (await module.get_change_risk("HEAD", baseline=0))["health_delta"]

    assert delta["analyzer"]["analyzer_version"] > 0
    assert delta["base"]["sha"] and delta["head"]["sha"]
    assert delta["basis"] == "both_sides_analyzed"


# -- caching and concurrency ------------------------------------------------


def test_identical_concurrent_comparisons_run_once(make_repo, monkeypatch):
    repo = seeded(make_repo)
    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    calls = {"n": 0}
    original = service.analyzer.analyze

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service.analyzer, "analyze", counted)
    request = DeltaRequest(str(repo.path), "HEAD")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [f.result() for f in [pool.submit(service.compare, request) for _ in range(4)]]

    # Two analyses (base and head) for one comparison, however many callers ask.
    assert calls["n"] == 2
    assert all(r.introduced_total == results[0].introduced_total for r in results)


def test_a_working_tree_comparison_is_keyed_by_content_not_by_head(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    request = DeltaRequest(str(repo.path), None)

    repo.write("app.py", python_complex("tangle", 18))
    first = service.compare(request)
    repo.write("app.py", "x = 1\n")
    second = service.compare(request)

    assert first.introduced_total >= 1
    assert second.introduced_total == 0
