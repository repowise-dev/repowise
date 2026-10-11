"""Unit tests for GET /api/repos/{repo_id}/savings.

The endpoint reads the canonical ledger through the one core report service,
so these tests are as much about what it must *not* do -- reprice history,
credit a dead end, or collapse two calls to one tool -- as about the totals.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.distill.store import OmissionStore
from repowise.core.savings import recorder
from repowise.core.savings.correlation import new_event_id, scoped_idempotency_key

from .conftest import create_test_repo

#: Sonnet's input rate per million, the table's default tier. Written out so an
#: expected dollar figure below is hand-checkable rather than recomputed with
#: the expression under test.
SONNET_INPUT = 3.0


def _sidecar(repo_dir: Path) -> None:
    """Create the sidecar. The recorder never creates one; absence is opt-out."""
    OmissionStore(repo_dir / ".repowise" / "omissions" / "omissions.db").close()


def _event(
    repo_dir: Path,
    *,
    surface: str = "mcp",
    agent: str = "claude_code",
    operation: str = "get_answer",
    baseline: int = 1_000,
    delivered: int = 0,
    result_state: str = "success",
    is_usable: bool = True,
    evidence_kind: str = "measured",
    priced: bool = False,
    occurred_at: str | None = None,
) -> None:
    event_id = new_event_id()
    payload: dict = {
        "event_id": event_id,
        "idempotency_key": scoped_idempotency_key(str(repo_dir), surface, event_id),
        "occurred_at": occurred_at or _now(),
        "surface": surface,
        "integration": agent,
        "agent": agent,
        "operation": operation,
        "evidence_kind": evidence_kind,
        "estimator": "chars_per_token_floor_v1",
        "token_unit": "estimated_tokens",
        "result_state": result_state,
        "is_usable": is_usable,
        "baseline_input_tokens": baseline,
        "pre_budget_input_tokens": baseline,
        "delivered_input_tokens": delivered,
    }
    if priced:
        payload.update(
            model="claude-sonnet-4-6",
            currency="USD",
            pricing_source="session_model:claude_code",
            pricing_version="pricing:test",
            input_rate_usd_per_million=SONNET_INPUT,
            output_rate_usd_per_million=15.0,
        )
    assert recorder.record_event(repo_dir, payload) is True


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


async def test_savings_endpoint_returns_canonical_totals_and_breakdowns(
    client: AsyncClient, tmp_path: Path
) -> None:
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    _event(repo_dir, surface="mcp", agent="claude_code", baseline=1_000)
    _event(repo_dir, surface="distill", agent="codex", operation="git_log", baseline=400)

    resp = await client.get(f"/api/repos/{repo['id']}/savings")
    assert resp.status_code == 200
    data = resp.json()

    assert data["available"] is True
    assert data["unique_events"] == 2
    assert data["saved_input_tokens"] == 1_400
    surfaces = {row["group"]: row for row in data["per_surface"]}
    assert surfaces["mcp"]["saved_input_tokens"] == 1_000
    assert surfaces["distill"]["saved_input_tokens"] == 400
    # The display name rides on the payload, so no consumer needs a label map.
    agents = {row["agent"]: row for row in data["per_agent"]}
    assert agents["claude_code"]["agent_display_name"] == "Claude Code"
    assert agents["codex"]["agent_display_name"] == "Codex CLI"
    assert data["last_event_at"]


async def test_priced_and_unpriced_savings_are_reported_separately(
    client: AsyncClient, tmp_path: Path
) -> None:
    """An event carries the rate it was worth; one without a rate is not guessed at."""
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    _event(repo_dir, baseline=1_000, priced=True)
    _event(repo_dir, baseline=500, priced=False)

    data = (await client.get(f"/api/repos/{repo['id']}/savings")).json()

    assert data["saved_input_tokens"] == 1_500
    assert data["priced_saved_input_tokens"] == 1_000
    assert data["unpriced_saved_input_tokens"] == 500
    # 1_000 tokens at $3/M. Only the priced half contributes.
    assert data["priced_input_savings_usd"] == pytest.approx(1_000 * SONNET_INPUT / 1_000_000)
    models = {row["group"]: row for row in data["per_model"]}
    assert models["claude-sonnet-4-6"]["saved_input_tokens"] == 1_000
    # The unpriced bucket is a real row, not a dropped one.
    assert models[None]["saved_input_tokens"] == 500


async def test_a_dead_end_saves_nothing_and_is_not_an_answered_query(
    client: AsyncClient, tmp_path: Path
) -> None:
    """The legacy ledger wrote a dead end as a *negative* saving and counted it
    as a query answered. Both contradict the contract: a call that answered
    nothing saved nothing, and it did not answer."""
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    _event(repo_dir, baseline=1_000, delivered=0)
    _event(repo_dir, baseline=0, delivered=800, result_state="dead_end", is_usable=False)

    data = (await client.get(f"/api/repos/{repo['id']}/savings")).json()

    assert data["dead_ends"] == 1
    assert data["mcp_queries_answered"] == 1  # the success, not the dead end
    # The dead end contributed zero rather than -800.
    assert data["saved_input_tokens"] == 1_000


async def test_two_calls_to_one_tool_stay_independent(
    client: AsyncClient, tmp_path: Path
) -> None:
    """The legacy summary applied counterfactual precedence per *tool*, so one
    counterfactual row discarded every truncation row for that tool, including
    unrelated later calls. The canonical ledger correlates per interaction."""
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    _event(repo_dir, operation="get_risk", baseline=1_000, evidence_kind="inferred")
    _event(repo_dir, operation="get_risk", baseline=700, evidence_kind="measured")

    data = (await client.get(f"/api/repos/{repo['id']}/savings")).json()

    assert data["unique_events"] == 2
    assert data["saved_input_tokens"] == 1_700  # neither call discards the other
    assert data["inferred_saved_input_tokens"] == 1_000
    assert data["measured_saved_input_tokens"] == 700
    operations = {row["group"]: row for row in data["per_operation"]}
    assert operations["get_risk"]["events"] == 2


async def test_no_fixed_output_credit_is_added(client: AsyncClient, tmp_path: Path) -> None:
    """The endpoint used to add a flat 60 output tokens per answered query. A
    saving with no output evidence now reports none rather than a invented one."""
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    _event(repo_dir, baseline=1_000, priced=True)

    data = (await client.get(f"/api/repos/{repo['id']}/savings")).json()

    assert data["saved_output_tokens"] is None
    assert data["priced_output_savings_usd"] == 0.0
    # The dollar figure is exactly the input side, with nothing added on top.
    assert data["priced_input_savings_usd"] == pytest.approx(1_000 * SONNET_INPUT / 1_000_000)


async def test_a_repository_with_no_sidecar_is_unavailable(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Unavailable is not zero: nothing has been measured here."""
    repo = await create_test_repo(client, tmp_path)
    data = (await client.get(f"/api/repos/{repo['id']}/savings")).json()
    assert data["available"] is False
    assert data["saved_input_tokens"] == 0


async def test_the_window_excludes_older_events(client: AsyncClient, tmp_path: Path) -> None:
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    _event(repo_dir, baseline=1_000)
    _event(repo_dir, baseline=400, occurred_at="2020-01-01T00:00:00.000000Z")

    everything = (await client.get(f"/api/repos/{repo['id']}/savings")).json()
    recent = (
        await client.get(f"/api/repos/{repo['id']}/savings", params={"days": 1})
    ).json()

    assert everything["saved_input_tokens"] == 1_400
    assert recent["saved_input_tokens"] == 1_000
    assert recent["window_days"] == 1


async def test_unknown_repo_404(client: AsyncClient) -> None:
    resp = await client.get("/api/repos/nope/savings")
    assert resp.status_code == 404


async def test_observed_opportunities_ride_along_without_entering_the_total(
    client: AsyncClient, tmp_path: Path, monkeypatch
) -> None:
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    _event(repo_dir, baseline=1_000)

    import repowise.core.distill.missed as missed

    monkeypatch.setattr(
        missed,
        "scan_missed_savings",
        lambda root, **kw: {
            "events": 3,
            "raw_tokens": 9_000,
            "est_saved_tokens": 4_200,
            "per_filter": {},
            "window_days": 7.0,
        },
    )
    data = (await client.get(f"/api/repos/{repo['id']}/savings")).json()

    assert data["missed_events"] == 3
    assert data["missed_tokens_est"] == 4_200
    # The headline is untouched by what was *not* saved.
    assert data["saved_input_tokens"] == 1_000


async def test_the_endpoint_and_the_core_report_agree(
    client: AsyncClient, tmp_path: Path
) -> None:
    """The phase's exit criterion, asserted rather than assumed.

    If the endpoint ever recomputes a total from parts instead of reading the
    report, this is what catches it.
    """
    from repowise.core.savings.service import load_report

    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    _event(repo_dir, surface="mcp", baseline=1_000, priced=True)
    _event(repo_dir, surface="hook", agent="codex", baseline=250)
    _event(repo_dir, baseline=0, delivered=90, result_state="dead_end", is_usable=False)

    data = (await client.get(f"/api/repos/{repo['id']}/savings")).json()
    report = load_report(repo_dir)
    assert report is not None

    for field in (
        "unique_events",
        "saved_input_tokens",
        "measured_saved_input_tokens",
        "inferred_saved_input_tokens",
        "priced_saved_input_tokens",
        "unpriced_saved_input_tokens",
        "priced_input_savings_usd",
        "mcp_queries_answered",
        "dead_ends",
        # A named allowlist, so a field added to the report and forgotten at
        # the endpoint would pass this test silently. Added here on purpose.
        "baseline_events",
        "reducing_events",
        "baseline_input_tokens",
        "baseline_saved_input_tokens",
        "input_reduction_ratio",
        "input_reduction_ratio_p90",
    ):
        assert data[field] == pytest.approx(getattr(report, field)), field
