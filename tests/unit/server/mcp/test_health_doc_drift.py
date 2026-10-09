"""``get_health(include=["doc_drift"])`` — the agent-facing drift surface.

Two things are load-bearing and neither is caught by the generic budget tests.
``test_every_shed_path_names_a_real_block`` skips a top-level block the recorded
shape does not contain, and this block is absent unless it is asked for, so the
shed path is unverified there. And the block's ``basis`` is the only thing
stopping a finding count from reading as a coverage claim.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from repowise.core.analysis.doc_drift.constants import DETECTION_BASIS
from repowise.core.persistence.models import DocDriftFinding


def _finding(repo_id: str, path: str, line: int, **kw) -> DocDriftFinding:
    return DocDriftFinding(
        repository_id=repo_id,
        file_path=path,
        line_number=line,
        kind=kw.get("kind", "path"),
        target=kw.get("target", "src/gone.py"),
        confidence=kw.get("confidence", 0.9),
        reason=kw.get("reason", "Document names src/gone.py, which no longer exists."),
        origin=kw.get("origin", "path_no_candidate"),
        evidence_json='["docs/a.md:1 states `src/gone.py`"]',
        raw="src/gone.py",
        context="see src/gone.py",
    )


@pytest.fixture
async def drift_rows(session, repo_id):
    session.add_all(
        [
            _finding(repo_id, "docs/a.md", 7),
            _finding(repo_id, "docs/a.md", 19, kind="anchor", confidence=0.95),
            _finding(repo_id, "docs/b.md", 3, confidence=0.5, origin="path_no_candidate_in_guide"),
        ]
    )
    await session.commit()
    return repo_id


@pytest.mark.asyncio
async def test_the_block_is_absent_until_it_is_asked_for(setup_mcp, drift_rows):
    from repowise.server.mcp_server import get_health

    assert "doc_drift" not in await get_health()


@pytest.mark.asyncio
async def test_doc_drift_is_a_known_include_key(setup_mcp, drift_rows):
    """An unknown key is reported rather than ignored, so this would be visible."""
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["doc_drift"])
    assert "doc_drift" not in (result.get("unknown_include_keys") or [])


@pytest.mark.asyncio
async def test_the_block_carries_findings_totals_and_its_basis(setup_mcp, drift_rows):
    from repowise.server.mcp_server import get_health

    block = (await get_health(include=["doc_drift"]))["doc_drift"]

    assert block["findings_total"] == 3
    assert block["documents"] == 2
    assert block["confidence"] == {"high": 2, "medium": 1, "low": 0}
    # Without this a reader takes three findings as the whole truth about the
    # documentation, when most references were never checkable.
    assert block["findings_basis"] == DETECTION_BASIS

    lead = block["findings"][0]
    # Filed against the DOCUMENT, not the target it names.
    assert lead["file_path"].endswith(".md")
    assert lead["target"] == "src/gone.py"
    assert {"line_number", "kind", "origin", "reason", "raw", "context"} <= set(lead)
    # Budgeted: the evidence lines are the CLI's job.
    assert "evidence" not in lead


@pytest.mark.asyncio
async def test_targets_narrow_drift_to_those_documents(setup_mcp, drift_rows):
    """A finding is filed against the document, so the file scope is the doc scope."""
    from repowise.server.mcp_server import get_health

    block = (await get_health(targets=["docs/a.md"], include=["doc_drift"]))["doc_drift"]

    assert block["findings_total"] == 2
    assert {f["file_path"] for f in block["findings"]} == {"docs/a.md"}


@pytest.mark.asyncio
async def test_limit_caps_the_list_and_says_so(setup_mcp, drift_rows):
    from repowise.server.mcp_server import get_health

    block = (await get_health(include=["doc_drift"], limit=1))["doc_drift"]

    assert block["findings_emitted"] == 1
    assert block["findings_total"] == 3
    assert block["findings_reduced_reason"] == "limit"


@pytest.mark.asyncio
async def test_production_scope_does_not_report_every_document_as_clean(
    setup_mcp, health_data, drift_rows
):
    """The regression this exists for: a scope wipe rendering as a clean tree.

    ``scope="production"`` narrows every block to the files carrying a health
    metric. No markdown file carries one, so routing drift rows through that
    filter reported all 19 real findings on this repository as zero, on a
    supported argument, with nothing in the response saying why.
    """
    from repowise.server.mcp_server import get_health

    block = (await get_health(include=["doc_drift"], scope="production"))["doc_drift"]

    assert block["findings_total"] == 3


@pytest.mark.asyncio
async def test_an_index_without_the_drift_table_says_so(
    setup_mcp, health_data, drift_rows, monkeypatch
):
    """"Could not read" must never render as "nothing to report"."""
    import repowise.server.mcp_server.tool_health.loading as th
    from repowise.server.mcp_server import get_health

    async def _raise(*_a, **_k):
        raise OperationalError("select", {}, Exception("no such table: doc_drift_findings"))

    monkeypatch.setattr(th, "get_doc_drift_findings", _raise)

    result = await get_health(include=["doc_drift"])

    assert result["doc_drift"] == {"unavailable": "index_predates_doc_drift"}
    # And the failed read did not take the rest of the response with it.
    assert result["kpis"]["file_count"] > 0


@pytest.mark.asyncio
async def test_the_include_token_really_reaches_the_budget_as_a_request(setup_mcp, drift_rows):
    """Assert the wiring, not a hand-built set: the token has to survive
    ``_requested_shed_keys`` for the deferral to mean anything in production."""
    import inspect

    from repowise.server.mcp_server._budget.contracts import _CONTRACTS, _requested_shed_keys
    from repowise.server.mcp_server.tool_health import get_health as raw_get_health

    keys = _requested_shed_keys(
        _CONTRACTS["get_health"],
        inspect.signature(raw_get_health),
        (),
        {"include": ["doc_drift"]},
    )
    assert "doc_drift.findings" in keys

    # And a call that did not ask for it is entitled to nothing.
    assert not _requested_shed_keys(
        _CONTRACTS["get_health"],
        inspect.signature(raw_get_health),
        (),
        {"targets": ["src/a.py"]},
    )


def test_the_shed_path_names_the_block_the_tool_actually_emits():
    """A shed path that does not resolve sheds nothing, silently.

    The recorded-shape sweep cannot catch this one: it skips a top-level block
    the recording does not contain, and this block only exists under ``include``.
    """
    from repowise.server.mcp_server._budget.contracts import _CONTRACTS

    contract = _CONTRACTS["get_health"]
    assert "doc_drift.findings[]" in contract.shed_order
    assert ("doc_drift", ("doc_drift.findings[]",)) in contract.requested_projections


def test_asking_for_drift_defers_it_to_the_back_of_the_shed_order():
    """Otherwise the only caller who wants the block is the one who loses it."""
    from repowise.server.mcp_server._budget.contracts import (
        _CONTRACTS,
        _prioritised_shed_order,
    )

    order = _CONTRACTS["get_health"].shed_order
    assert order.index("doc_drift.findings[]") < order.index("findings[]")

    deferred = _prioritised_shed_order(order, frozenset({"doc_drift.findings"}))
    assert deferred[-1] == "doc_drift.findings[]"
    # Nothing else moved.
    assert [k for k in deferred if k != "doc_drift.findings[]"] == [
        k for k in order if k != "doc_drift.findings[]"
    ]
