"""One canonical savings event per MCP invocation, measured when it is final.

Driven through the real ``tool_middleware`` composition rather than through
``instrument`` alone, because the defect these tests pin is an ordering defect:
every individual layer was correct and the number was still wrong. A test that
wraps one layer cannot see that, which is why the old ledger row was measured
five layers deep for as long as it was.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.distill.store import OmissionStore
from repowise.server.mcp_server import _state, tool_middleware
from repowise.server.mcp_server._savings import declare_replaced


def _repo_db(repo: Path) -> Path:
    return repo / ".repowise" / "omissions" / "omissions.db"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    OmissionStore(_repo_db(tmp_path)).close()
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path), raising=False)
    return tmp_path


def _events(repo: Path) -> list[dict]:
    with OmissionStore(_repo_db(repo)) as store:
        store._conn.row_factory = __import__("sqlite3").Row
        rows = store._conn.execute("SELECT * FROM savings_events ORDER BY occurred_at").fetchall()
    return [dict(row) for row in rows]


def _report(repo: Path):
    with OmissionStore(_repo_db(repo)) as store:
        return store.savings().report(str(repo), as_of=datetime.now(UTC))


@pytest.mark.asyncio
async def test_one_invocation_produces_exactly_one_event(repo: Path) -> None:
    async def get_context(targets: list[str], compact: bool = True) -> dict:
        return {
            "targets": {targets[0]: {"skeleton": {"tokens": 200, "full_tokens": 4000}}},
            "_meta": {"timing_ms": 1.0},
        }

    await tool_middleware(get_context)(["a.py"])

    events = _events(repo)
    assert len(events) == 1
    event = events[0]
    assert event["surface"] == "mcp"
    assert event["operation"] == "get_context"
    assert event["result_state"] == "success"
    assert event["is_usable"] == 1
    assert event["estimator"] == "chars_per_token_floor_v1"
    assert event["token_unit"] == "estimated_tokens"
    assert event["schema_version"] == 1


@pytest.mark.asyncio
async def test_a_counterfactual_is_inferred_and_the_drop_is_not_added_twice(
    repo: Path,
) -> None:
    """``saved = replaced - delivered``, with truncation kept as diagnostic.

    The correlated drop is evidence *inside* this interaction, so adding it to
    the counterfactual would count one interaction's saving twice.
    """

    async def get_symbol(symbol_id: str) -> dict:
        response = {"symbol_id": symbol_id, "source": "def f(): ...", "_meta": {}}
        declare_replaced(response, 5000)
        return response

    await tool_middleware(get_symbol)("a.py::f")

    event = _events(repo)[0]
    assert event["evidence_kind"] == "inferred"
    assert event["baseline_input_tokens"] == 5000
    delivered = event["delivered_input_tokens"]
    assert event["saved_input_tokens"] == 5000 - delivered
    # The truncation is still recorded, as its own dimension, so the diagnostic
    # survives without the counterfactual absorbing it.
    assert event["dropped_input_tokens"] == max(event["pre_budget_input_tokens"] - delivered, 0)


@pytest.mark.asyncio
async def test_delivered_is_measured_after_the_outermost_budget(repo: Path) -> None:
    """The ordering fix, stated as a number.

    ``instrument`` sits five layers deep. After it, ``timed`` stamps ``_meta``
    and the outer budget runs the budgeter twice more. So the delivered size has
    to be at least the size of everything stamped after the old measurement
    point, and the event's own ``_meta`` fields have to be inside it.
    """

    async def get_context(targets: list[str], compact: bool = True) -> dict:
        return {
            "targets": {targets[0]: {"skeleton": {"tokens": 200, "full_tokens": 4000}}},
            "_meta": {},
        }

    out = await tool_middleware(get_context)(["a.py"])
    event = _events(repo)[0]

    # timing_ms is stamped by a layer outside the old measurement point.
    assert out["_meta"]["timing_ms"] is not None
    import json

    final_chars = len(json.dumps(out, separators=(",", ":"), default=str))
    assert event["delivered_input_tokens"] == pytest.approx(final_chars // 4, abs=2)


@pytest.mark.asyncio
async def test_two_calls_to_the_same_tool_stay_independent(repo: Path) -> None:
    """Deduplication is per interaction, never per tool name.

    The legacy summary applied counterfactual precedence across a whole tool, so
    one counterfactual row discarded every truncation row for that tool,
    including later unrelated calls.
    """

    async def get_risk(targets: list[str]) -> dict:
        return {"targets": [{"path": targets[0]}], "_meta": {}}

    wrapped = tool_middleware(get_risk)
    await wrapped(["a.py"])
    await wrapped(["b.py"])

    events = _events(repo)
    assert len(events) == 2
    assert len({event["event_id"] for event in events}) == 2
    assert len({event["idempotency_key"] for event in events}) == 2
    assert _report(repo).unique_events == 2


@pytest.mark.asyncio
async def test_an_error_saves_nothing_and_is_not_an_answered_query(repo: Path) -> None:
    """The legacy ledger wrote a *negative* saving here.

    That is how a session that net-spent could still report a credit. Under the
    contract a failed call saves zero and its delivered overhead is diagnostic.
    """

    async def get_why(query: str) -> dict:
        return {"error": "not indexed", "_meta": {}}

    await tool_middleware(get_why)("x")

    event = _events(repo)[0]
    assert event["result_state"] == "error"
    assert event["is_usable"] == 0
    assert event["saved_input_tokens"] == 0
    assert event["delivered_input_tokens"] > 0

    report = _report(repo)
    assert report.saved_input_tokens == 0
    assert report.mcp_queries_answered == 0
    assert report.saving_interactions == 0


@pytest.mark.asyncio
async def test_a_mutating_tool_can_neither_earn_credit_nor_take_a_debit(
    repo: Path,
) -> None:
    """``set_finding_status`` has no counterfactual, and should not have one.

    It changes state rather than replacing exploration, so there is nothing it
    could have saved. The explicit decision is that it records a zero-saving
    success, not that it is exempt from the ledger: an event with
    ``saved = 0`` is how "this ran and saved nothing" is stated.
    """

    async def set_finding_status(finding_id: str, status: str) -> dict:
        return {"ok": True, "_meta": {}}

    await tool_middleware(set_finding_status)("f1", "accepted")

    event = _events(repo)[0]
    assert event["evidence_kind"] == "measured"
    assert event["baseline_input_tokens"] is None
    assert event["saved_input_tokens"] == 0
    assert event["result_state"] == "success"


@pytest.mark.asyncio
async def test_attribution_is_unknown_without_an_announced_client(repo: Path) -> None:
    """No clientInfo is reachable in-process, so nothing may be guessed.

    Specifically not ``claude_code``, which is what the CLI falls back to when
    it detects nothing; using it here would file every anonymous client's
    traffic under one agent.
    """

    async def get_health(path: str) -> dict:
        return {"score": 7.0, "_meta": {}}

    await tool_middleware(get_health)("a.py")

    event = _events(repo)[0]
    assert event["agent"] == "unknown"
    assert event["integration"] == "unknown"


@pytest.mark.asyncio
async def test_a_repository_without_a_sidecar_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent sidecar is an opt-out, and a tool call must still succeed."""
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path), raising=False)

    async def get_overview() -> dict:
        return {"summary": "x", "_meta": {}}

    out = await tool_middleware(get_overview)()
    assert out["summary"] == "x"
    assert not _repo_db(tmp_path).exists()


@pytest.mark.asyncio
async def test_the_tool_still_answers_when_recording_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accounting is never allowed to fail a user's tool call."""
    from repowise.server.mcp_server._savings import event as savings_event

    def explode(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("ledger on fire")

    monkeypatch.setattr(savings_event, "_record", explode)

    async def get_dead_code() -> dict:
        return {"files": [], "_meta": {}}

    out = await tool_middleware(get_dead_code)()
    assert out["files"] == []
    assert _events(repo) == []


@pytest.mark.asyncio
async def test_the_raw_size_is_observed_before_anything_is_shed(repo: Path) -> None:
    """Which layer wins the measurement, stated as a number.

    ``observe_pre_budget`` is called by both budget layers and by
    ``instrument``, and takes the first value only. The innermost budget runs
    first, so the value that lands is the untrimmed tool output. If that order
    ever inverted, the recorded raw size would be a post-shed size and the
    measured truncation would collapse to almost nothing -- on this response,
    from thousands of tokens to under two hundred.
    """
    from repowise.server.mcp_server._savings import interaction as interaction_module

    observed: list[tuple[int | None, bool]] = []
    original = interaction_module.Interaction.observe_pre_budget

    def traced(self: interaction_module.Interaction, tokens: int | None) -> None:
        accepted = self.pre_budget_input_tokens is None and tokens is not None
        observed.append((tokens, accepted))
        original(self, tokens)

    interaction_module.Interaction.observe_pre_budget = traced  # type: ignore[method-assign]
    try:

        async def get_context(targets: list[str]) -> dict:
            return {
                "targets": {targets[0]: {"skeleton": {"tokens": 200, "full_tokens": 4000}}},
                "filler": ["y" * 400 for _ in range(60)],
                "_meta": {},
            }

        await tool_middleware(get_context)(["a.py"])
    finally:
        interaction_module.Interaction.observe_pre_budget = original  # type: ignore[method-assign]

    # Three observations; only the first is kept, and it is by far the largest.
    assert [accepted for _, accepted in observed] == [True, False, False]
    kept = observed[0][0]
    assert kept is not None
    assert all(kept > later for later, _ in observed[1:])

    event = _events(repo)[0]
    assert event["pre_budget_input_tokens"] == kept
    assert event["dropped_input_tokens"] == kept - event["delivered_input_tokens"]


@pytest.mark.asyncio
async def test_a_truncated_response_links_its_recovery_reference(repo: Path) -> None:
    """The event points at what was dropped, and dropping it is not optional.

    The response carries refs as ``repowise#<hex>`` while the store and the
    ledger's foreign key use the bare hex. Passing the public shape through
    failed validation and took the *whole event* with it -- silently, and on
    exactly the truncated responses where the saving is largest. So this
    asserts the link, not just that an event exists.
    """

    async def get_context(targets: list[str]) -> dict:
        return {
            "targets": {targets[0]: {"skeleton": {"tokens": 200, "full_tokens": 4000}}},
            "filler": ["y" * 400 for _ in range(60)],
            "_meta": {},
        }

    out = await tool_middleware(get_context)(["a.py"])
    assert out["_meta"]["omitted"]["refs"], "expected this response to be truncated"

    with OmissionStore(_repo_db(repo)) as store:
        linked = [
            row[0]
            for row in store._conn.execute("SELECT omission_ref FROM savings_event_omissions")
        ]
        stored = [row[0] for row in store._conn.execute("SELECT ref FROM omissions")]
    assert linked, "a truncated response recorded no recovery link"
    assert set(linked) <= set(stored)
    for ref in linked:
        assert not ref.startswith("repowise#")
        assert store_ref_is_bare_hex(ref)


def store_ref_is_bare_hex(ref: str) -> bool:
    from repowise.core.distill.markers import is_valid_ref

    return is_valid_ref(ref)
