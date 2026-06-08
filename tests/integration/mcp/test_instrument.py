"""instrument() — records a ledger row without changing user-facing output.

Exercises the full savings seam end to end against a real omission sidecar: a
wrapped tool's response is byte-identical (save for additive ``_meta`` fields)
and produces a ``mcp:<tool>`` ledger row; an unwrapped tool produces none.
"""

from __future__ import annotations

import copy
import inspect
from pathlib import Path

import pytest

from repowise.core.distill.store import OmissionStore
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._savings import declare_replaced, instrument


def _repo_db(repo: Path) -> Path:
    return repo / ".repowise" / "omissions" / "omissions.db"


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo with an opt-in sidecar, set as the MCP server's scoped repo."""
    OmissionStore(_repo_db(tmp_path)).close()
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path), raising=False)
    return tmp_path


def _ledger(repo: Path) -> dict[str, dict]:
    store = OmissionStore(_repo_db(repo))
    try:
        return {r["group"]: r for r in store.savings_rollup(by="source")}
    finally:
        store.close()


async def get_context(targets: list[str], compact: bool = True) -> dict:
    """A get_context-shaped tool whose skeleton drives the generic estimator."""
    return {
        "targets": {targets[0]: {"skeleton": {"tokens": 200, "full_tokens": 4000}}},
        "_meta": {"timing_ms": 1.0},
    }


async def get_symbol(symbol_id: str) -> dict:
    """A get_symbol-shaped tool that declares an exact counterfactual."""
    response = {"symbol_id": symbol_id, "source": "def f(): ...", "_meta": {}}
    declare_replaced(response, 5000)
    return response


@pytest.mark.asyncio
async def test_wrapped_tool_records_and_preserves_output(repo: Path) -> None:
    raw = await get_context(["a.py"])
    expected = copy.deepcopy(raw)

    wrapped = instrument(get_context)
    out = await wrapped(["a.py"])

    # User-facing payload unchanged; only additive _meta savings fields appear.
    assert out["targets"] == expected["targets"]
    assert out["_meta"]["timing_ms"] == 1.0
    assert out["_meta"]["replaced_tokens"] == 4000
    assert out["_meta"]["tokens_saved"] > 0

    row = _ledger(repo)["mcp:get_context"]
    assert row["raw_tokens"] == 4000
    assert row["events"] == 1


@pytest.mark.asyncio
async def test_declared_counterfactual_wins(repo: Path) -> None:
    wrapped = instrument(get_symbol)
    out = await wrapped("a.py::f")
    assert out["_meta"]["replaced_tokens"] == 5000
    assert _ledger(repo)["mcp:get_symbol"]["raw_tokens"] == 5000


@pytest.mark.asyncio
async def test_unwrapped_tool_records_nothing(repo: Path) -> None:
    await get_context(["a.py"])  # called directly, no middleware
    assert _ledger(repo) == {}


@pytest.mark.asyncio
async def test_instrument_preserves_signature() -> None:
    wrapped = instrument(get_context)
    assert inspect.signature(wrapped) == inspect.signature(get_context)
    assert wrapped.__name__ == "get_context"


@pytest.mark.asyncio
async def test_no_saving_writes_no_row(repo: Path) -> None:
    async def search_codebase(query: str) -> dict:
        return {"results": [], "_meta": {}}  # nothing cited → no counterfactual

    out = await instrument(search_codebase)("x")
    assert "replaced_tokens" not in out["_meta"]
    assert _ledger(repo) == {}
