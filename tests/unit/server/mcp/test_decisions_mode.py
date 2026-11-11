"""``decisions.mode: off`` silences the MCP decision surfaces; read_only keeps them."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server import _answer_context
from repowise.server.mcp_server.tool_why import tool as why_tool


def _repo(tmp_path: Path, mode: str) -> Path:
    (tmp_path / ".repowise").mkdir(exist_ok=True)
    (tmp_path / ".repowise" / "config.yaml").write_text(
        f"decisions:\n  mode: '{mode}'\n", encoding="utf-8"
    )
    return tmp_path


def _serve_from(monkeypatch, root: Path) -> list:
    """Point get_why at *root*; returns the calls that reached a mode."""
    calls: list = []

    async def resolve(_repo):
        return SimpleNamespace(path=root)

    async def dispatch(*args):
        calls.append(args)
        return {"answered": True}

    monkeypatch.setattr(why_tool, "_resolve_repo_context", resolve)
    monkeypatch.setattr(why_tool, "_dispatch", dispatch)
    return calls


@pytest.mark.asyncio
async def test_get_why_says_the_layer_is_off_and_reads_nothing(tmp_path, monkeypatch):
    calls = _serve_from(monkeypatch, _repo(tmp_path, "off"))

    result = await why_tool.get_why(targets=["src/a.py"])

    assert result == {"decisions": "off", "note": "Decisions are off for this repository."}
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["read_only", "full"])
async def test_get_why_answers_in_read_only_and_full(tmp_path, monkeypatch, mode):
    calls = _serve_from(monkeypatch, _repo(tmp_path, mode))

    assert (await why_tool.get_why(targets=["src/a.py"]))["answered"] is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_answer_reads_no_decision_when_off(tmp_path, monkeypatch):
    async def boom(*_a, **_k):
        pytest.fail("decisions were read")

    monkeypatch.setattr(_answer_context, "_decisions_answering", boom)
    ctx = SimpleNamespace(path=_repo(tmp_path, "off"))

    assert (
        await _answer_context.fetch_relevant_decisions(ctx, "r1", ["src/a.py"], question="why x")
        == []
    )


@pytest.mark.asyncio
async def test_get_context_and_get_risk_name_no_decision_when_off(tmp_path):
    from repowise.server.mcp_server.tool_context import sections
    from repowise.server.mcp_server.tool_risk.directives import _governance_directive

    root = _repo(tmp_path, "off")
    card = SimpleNamespace(
        query=SimpleNamespace(repo_root=root, repository=None), data={}, target="src/a.py"
    )

    await sections._governing_decision_titles(card, "src/a.py")
    assert card.data == {}
    # No session is opened: the factory would fail if it were.
    assert await _governance_directive(SimpleNamespace(path=root, session_factory=None), ["a"]) == []
