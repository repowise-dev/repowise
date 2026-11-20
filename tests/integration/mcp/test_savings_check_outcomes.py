"""Check tools record what they returned, read off the reply in hand.

Driven through the real ``tool_middleware`` so the outcome is read where the
event is written, on the reply the agent received. ``tool_why`` is loaded up
front, as it is whenever get_why is served, so its post-shed hook re-derives
``answer_basis`` from the rows in every run, not only after another test
happened to load it.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.distill.store import OmissionStore
from repowise.server.mcp_server import _state, tool_middleware
from repowise.server.mcp_server import tool_why as _tool_why  # noqa: F401
from repowise.server.mcp_server._savings import outcome as outcome_mod


def _repo_db(repo: Path) -> Path:
    return repo / ".repowise" / "omissions" / "omissions.db"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    OmissionStore(_repo_db(tmp_path)).close()
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path), raising=False)
    return tmp_path


def _outcomes(repo: Path) -> list[tuple[str, dict | None]]:
    with OmissionStore(_repo_db(repo)) as store:
        rows = store._conn.execute(
            "SELECT operation, outcome_json FROM savings_events ORDER BY occurred_at"
        ).fetchall()
    return [(op, json.loads(text) if text else None) for op, text in rows]


def _tool(name: str, reply: dict):
    async def tool() -> dict:
        return copy.deepcopy(reply)

    tool.__name__ = name
    return tool


CASES = [
    ("get_change_risk", {"risk_percentile": 81.2, "fix_history": {}}, {"level": "high"}),
    ("get_change_risk", {"risk_percentile": 50.0}, {"level": "medium"}),
    ("get_change_risk", {"risk_percentile": 4.0}, {"level": "low"}),
    ("get_change_risk", {"risk_percentile": None}, {}),
    (
        "get_risk",
        {
            "targets": {
                "a.py": {"hotspot_score": 0.95, "is_hotspot": True},
                "b.py": {"hotspot_score": 0.9, "is_hotspot": False},
                "c.py": {"hotspot_score": 0.5, "is_hotspot": False},
                "d.py": {"hotspot_score": 0.1, "is_hotspot": False},
                "e.py": {"error": "not found"},
            }
        },
        {"high": 1, "medium": 2, "low": 1},
    ),
    (
        "get_why",
        {
            "mode": "path",
            "answer_basis": "decision",
            "target_context": {
                "a.py": {"governing_decisions": [{"id": "1" * 32, "authority": "accepted"}]}
            },
        },
        {"decision": "accepted"},
    ),
    (
        "get_why",
        {
            "mode": "path",
            "answer_basis": "candidate",
            "target_context": {
                "a.py": {
                    "candidate_decisions": [{"id": "0" * 32, "authority": "candidate"}]
                }
            },
        },
        {"decision": "candidate"},
    ),
    ("get_why", {"mode": "search"}, {"decision": "none"}),
    (
        "get_health",
        {"mode": "targets", "targets": ["a.py", "b.py"], "findings": [{}], "findings_total": 7},
        {"findings": 7, "files": 2},
    ),
    (
        "get_health",
        {"top_findings": [{}, {}], "metrics": [{}, {}, {}]},
        {"findings": 2, "files": 3},
    ),
    (
        "get_dead_code",
        {"summary": {}, "tiers": {"high": {"count": 4}, "medium": {"count": 6}}},
        {"items": 10, "high_confidence": 4},
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "reply", "expected"), CASES)
async def test_each_check_tool_records_what_it_returned(
    repo: Path, tool: str, reply: dict, expected: dict
) -> None:
    await tool_middleware(_tool(tool, reply))()
    assert _outcomes(repo) == [(tool, expected)]


@pytest.mark.asyncio
async def test_other_tools_and_errors_record_no_outcome(repo: Path) -> None:
    await tool_middleware(_tool("get_answer", {"answer": "x"}))()
    await tool_middleware(_tool("get_risk", {"error": "no index"}))()
    assert [outcome for _, outcome in _outcomes(repo)] == [None, None]


@pytest.mark.asyncio
async def test_a_failing_outcome_leaves_the_reply_and_the_event(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = {"risk_percentile": 90.0, "fix_history": {"files": []}}
    expected = await tool_middleware(_tool("get_change_risk", reply))()

    def explode(*_args: object, **_kwargs: object) -> dict:
        raise RuntimeError("outcome on fire")

    monkeypatch.setattr(outcome_mod, "check_outcome", explode)
    out = await tool_middleware(_tool("get_change_risk", reply))()

    assert out == expected
    assert _outcomes(repo) == [("get_change_risk", {"level": "high"}), ("get_change_risk", None)]


@pytest.mark.asyncio
async def test_the_report_counts_recorded_outcomes(repo: Path) -> None:
    for tool, reply, _ in CASES:
        await tool_middleware(_tool(tool, reply))()
    with OmissionStore(_repo_db(repo)) as store:
        report = store.savings().report(str(repo), as_of=datetime.now(UTC))
    rows = {row["tool"]: row for row in report.checks}
    assert rows["get_change_risk"]["calls"] == 4
    assert (rows["get_change_risk"]["high"], rows["get_change_risk"]["low"]) == (1, 1)
    assert rows["get_why"]["decisions_accepted"] == 1
    assert rows["get_health"]["findings"] == 9
    assert rows["get_dead_code"]["high_confidence"] == 4
