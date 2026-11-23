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
    avoided_files: tuple = (),
    host_session_id: str | None = None,
    session_id: str | None = None,
    outcome: dict | None = None,
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
        "avoided_files": avoided_files,
        "host_session_id": host_session_id,
        "session_id": session_id,
    }
    if outcome is not None:
        payload["outcome"] = outcome
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
    # Each bucket carries its own priced dollars; the unpriced one adds nothing.
    assert models["claude-sonnet-4-6"]["priced_input_savings_usd"] == pytest.approx(
        data["priced_input_savings_usd"]
    )
    assert models[None]["priced_input_savings_usd"] == 0.0
    assert sum(row["priced_input_savings_usd"] for row in data["per_day"]) == pytest.approx(
        data["priced_input_savings_usd"]
    )


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


async def test_check_rows_follow_the_window_and_stay_null_unrecorded(
    client: AsyncClient, tmp_path: Path
) -> None:
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    _event(repo_dir, operation="get_change_risk", outcome={"level": "high"})
    _event(repo_dir, operation="get_change_risk")
    _event(repo_dir, operation="get_why", outcome={"decision": "accepted"})
    _event(
        repo_dir,
        operation="get_why",
        outcome={"decision": "none"},
        occurred_at="2020-01-01T00:00:00.000000Z",
    )
    _event(repo_dir, operation="get_dead_code")

    recent = (
        await client.get(f"/api/repos/{repo['id']}/savings", params={"days": 1})
    ).json()
    rows = {row["tool"]: row for row in recent["checks"]}
    assert list(rows) == [
        "get_change_risk",
        "get_why",
        "get_risk",
        "get_health",
        "get_dead_code",
    ]
    assert rows["get_change_risk"] == {
        "tool": "get_change_risk",
        "calls": 2,
        "outcome_calls": 1,
        "high": 1,
        "medium": 0,
        "low": 0,
        "decisions_accepted": None,
        "decisions_candidate": None,
        "decisions_none": None,
        "findings": None,
        "files": None,
        "items": None,
        "high_confidence": None,
    }
    assert (rows["get_why"]["calls"], rows["get_why"]["decisions_none"]) == (1, 0)
    assert (rows["get_dead_code"]["calls"], rows["get_dead_code"]["items"]) == (1, None)
    assert rows["get_risk"]["calls"] == 0

    everything = (await client.get(f"/api/repos/{repo['id']}/savings")).json()
    why = next(row for row in everything["checks"] if row["tool"] == "get_why")
    assert (why["calls"], why["decisions_none"]) == (2, 1)


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
        "files_not_opened",
        "files_not_opened_tokens",
        "tool_calls_answered",
    ):
        assert data[field] == pytest.approx(getattr(report, field)), field


async def test_files_not_opened_and_tool_calls_answered(
    client: AsyncClient, tmp_path: Path
) -> None:
    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    files = (("a.py", 700), ("b.py", 300))
    _event(repo_dir, avoided_files=files, host_session_id="s1")
    # Same session, same file, other surface: still one file not opened.
    _event(repo_dir, surface="hook", avoided_files=(("a.py", 900),), host_session_id="s1")
    _event(repo_dir, surface="distill", operation="git_log", baseline=400)

    data = (await client.get(f"/api/repos/{repo['id']}/savings")).json()
    assert data["files_not_opened"] == 2
    assert data["files_not_opened_tokens"] == 1_000
    assert data["tool_calls_answered"] == 2
    surfaces = {row["group"]: row for row in data["files_per_surface"]}
    assert surfaces["mcp"] == {
        "group": "mcp",
        "files_not_opened": 2,
        "files_not_opened_tokens": 1_000,
        "tool_calls_answered": 1,
    }
    assert surfaces["hook"]["files_not_opened"] == 0
    assert [row["group"] for row in data["files_per_agent"]] == ["claude_code"]
    assert len(data["files_per_day"]) == 1


async def test_output_saved_is_estimated_per_session_and_added_to_the_total(
    client: AsyncClient, tmp_path: Path
) -> None:
    from repowise.core.savings import output_estimate

    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    _event(repo_dir, baseline=1_000, priced=True, session_id="s1")
    _event(repo_dir, baseline=1_000, priced=True, session_id="s1")

    data = (await client.get(f"/api/repos/{repo['id']}/savings")).json()

    calibration = data["calibration"]
    assert calibration["version"] == output_estimate._load_calibration().version
    assert calibration["per_question"] is True
    assert calibration["formula"] == output_estimate.FORMULA
    assert calibration["sessions"] == 1
    row = calibration["per_model"][0]
    assert (row["model"], row["basis"], row["sessions"]) == ("claude-sonnet-4-6", "measured", 1)
    # Two calls is under one bench question's worth, so the floor of one applies.
    assert row["questions"] == 1
    assert data["output_tokens_saved_estimated"] == row["output_tokens_per_question"] > 0
    assert data["output_usd_saved_estimated"] == pytest.approx(
        row["output_tokens_per_question"] * 15.0 / 1_000_000
    )
    assert data["total_usd_saved_estimated"] == pytest.approx(
        data["priced_input_savings_usd"] + data["output_usd_saved_estimated"]
    )
    # The recorded output fields keep their meaning: nothing was measured.
    assert data["saved_output_tokens"] is None
    assert data["priced_output_savings_usd"] == 0.0


async def test_a_failing_output_estimate_leaves_the_rest_of_the_response_unchanged(
    client: AsyncClient, tmp_path: Path, monkeypatch
) -> None:
    from repowise.core.savings import output_estimate

    repo = await create_test_repo(client, tmp_path)
    repo_dir = Path(repo["local_path"])
    _sidecar(repo_dir)
    _event(repo_dir, baseline=1_000, priced=True, session_id="s1")

    before = (await client.get(f"/api/repos/{repo['id']}/savings")).json()

    def boom():
        raise RuntimeError("calibration unavailable")

    monkeypatch.setattr(output_estimate, "_load_calibration", boom)
    resp = await client.get(f"/api/repos/{repo['id']}/savings")
    assert resp.status_code == 200
    after = resp.json()

    for field in (
        "output_tokens_saved_estimated",
        "output_usd_saved_estimated",
        "total_usd_saved_estimated",
        "calibration",
    ):
        assert after.pop(field) is None
        before.pop(field)
    before.pop("as_of")
    after.pop("as_of")
    assert after == before
