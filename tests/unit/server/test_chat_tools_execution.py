"""The chat adapter's own branches: lookups, failures and serialization."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import repowise.server.mcp_server as mcp_mod
from repowise.core.registry import ToolEntry
from repowise.server import chat_tools


def _contract(entry: ToolEntry) -> chat_tools.ChatToolContract:
    return chat_tools.ChatToolContract(entry=entry, description="", parameters={})


@pytest.fixture
def one_tool(monkeypatch):
    """A catalog holding a single read-only tool with distinct metadata."""

    async def probe() -> dict:
        return {"ok": True}

    entry = ToolEntry(
        fn=probe,
        name="probe",
        artifact_type="risk",
        presentation="table",
        evidence_basis="measured",
    )
    monkeypatch.setattr(chat_tools, "get_tool_catalog", lambda _repo_path: [_contract(entry)])
    return entry


def test_an_entry_without_a_fastmcp_contract_is_left_out(monkeypatch, caplog) -> None:
    entry = ToolEntry(fn=lambda: None, name="orphan")
    monkeypatch.setattr(chat_tools, "selected_tool_entries", lambda _repo_path: [entry])
    monkeypatch.setattr(chat_tools, "get_registered_tool", lambda _name: None)

    assert chat_tools.get_tool_catalog(None) == []
    assert "orphan" in caplog.text


def test_artifact_metadata_comes_from_the_entry(one_tool) -> None:
    assert chat_tools.get_artifact_type("probe", None) == "risk"
    assert chat_tools.get_artifact_presentation("probe", None) == "table"
    assert chat_tools.get_artifact_evidence_basis("probe", None) == "measured"


def test_a_tool_outside_the_catalog_gets_the_generic_metadata(one_tool) -> None:
    assert chat_tools.get_artifact_type("missing", None) == "generic"
    assert chat_tools.get_artifact_presentation("missing", None) == "generic"
    assert chat_tools.get_artifact_evidence_basis("missing", None) == "unknown"


@pytest.mark.asyncio
async def test_a_tool_outside_the_catalog_is_refused(one_tool) -> None:
    result = await chat_tools.execute_tool("missing", {})

    assert result["error_code"] == "tool_not_enabled"
    assert "missing" in result["error"]


@pytest.mark.asyncio
async def test_a_tool_in_the_catalog_runs(one_tool) -> None:
    assert await chat_tools.execute_tool("probe", {}) == {"ok": True}


@pytest.mark.asyncio
async def test_a_failing_tool_returns_its_error_instead_of_raising() -> None:
    async def broken() -> dict:
        raise ValueError("bad input")

    result = await chat_tools.execute_entry(ToolEntry(fn=broken, name="broken"), {})

    assert result == {"error": "ValueError: bad input", "error_code": "tool_failed"}


@pytest.mark.asyncio
async def test_a_tool_without_a_repo_parameter_never_gets_one(monkeypatch) -> None:
    """The request's repo only backstops tools that accept a ``repo`` argument."""
    seen: dict = {}

    async def no_repo(**kwargs) -> dict:
        seen.update(kwargs)
        return {}

    registry = SimpleNamespace(get_all_aliases=lambda: ["boot"])
    monkeypatch.setattr(mcp_mod, "_registry", registry)

    await chat_tools.execute_entry(ToolEntry(fn=no_repo, name="no_repo"), {}, repo="boot")

    assert seen == {}


@dataclass
class _Point:
    x: int
    y: tuple


class _Opaque:
    __slots__ = ()

    def __str__(self) -> str:
        return "opaque"


@pytest.mark.asyncio
async def test_a_tool_result_is_made_json_serializable() -> None:
    async def rich() -> dict:
        return {
            1: (_Point(1, (2, 3)), None, True, 1.5),
            "nested": [{"k": _Opaque()}],
        }

    result = await chat_tools.execute_entry(ToolEntry(fn=rich, name="rich"), {})

    assert result == {
        "1": [{"x": 1, "y": [2, 3]}, None, True, 1.5],
        "nested": [{"k": "opaque"}],
    }


def test_init_tool_state_publishes_and_keeps_omitted_values(monkeypatch) -> None:
    for name in ("_session_factory", "_fts", "_vector_store", "_decision_store", "_repo_path"):
        monkeypatch.setattr(mcp_mod, name, getattr(mcp_mod, name, None))
    mcp_mod._decision_store = "kept-store"
    mcp_mod._repo_path = "/kept"

    chat_tools.init_tool_state("factory", "fts", "vectors")

    assert (mcp_mod._session_factory, mcp_mod._fts, mcp_mod._vector_store) == (
        "factory",
        "fts",
        "vectors",
    )
    assert (mcp_mod._decision_store, mcp_mod._repo_path) == ("kept-store", "/kept")

    chat_tools.init_tool_state("f2", "fts2", "v2", decision_store="store", repo_path="/repo")

    assert (mcp_mod._decision_store, mcp_mod._repo_path) == ("store", "/repo")
