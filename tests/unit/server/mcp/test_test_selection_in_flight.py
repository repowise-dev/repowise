"""Concurrent test selections in one repository share or queue, never fail as busy.

A second call for the change already running awaits that selection; a call for
another change waits for it to end and then runs its own. The running selection
is told to stop only when the last caller waiting on it leaves.
"""

from __future__ import annotations

import asyncio

import pytest

from repowise.core.analysis.changed_lines import ChangeSet, FileDiff
from repowise.server.mcp_server import _test_selection


def _change(path: str) -> ChangeSet:
    return ChangeSet(
        files={path: FileDiff(path=path)}, deleted=set(), label=path, base="b", head="h"
    )


@pytest.fixture
def gated(monkeypatch):
    """``_select`` that records each run and returns only once ``gate`` opens."""
    gate = asyncio.Event()
    runs: list = []

    async def _select(_repo, _factory, change, _config, cancel):
        runs.append((change, cancel))
        await gate.wait()
        return {"for": change.label}, None, None

    monkeypatch.setattr(_test_selection, "_select", _select)
    monkeypatch.setattr(_test_selection, "_IN_FLIGHT", {})
    return gate, runs


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def _call(tmp_path, change):
    return asyncio.ensure_future(_test_selection.select_change_tests(tmp_path, None, change))


@pytest.mark.asyncio
async def test_two_calls_for_one_change_share_the_running_selection(tmp_path, gated) -> None:
    gate, runs = gated
    first, second = _call(tmp_path, _change("a.py")), _call(tmp_path, _change("a.py"))
    await _settle()
    gate.set()

    assert (await first)[0] == (await second)[0] == {"for": "a.py"}
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_a_call_for_another_change_runs_after_the_running_one(tmp_path, gated) -> None:
    gate, runs = gated
    first = _call(tmp_path, _change("a.py"))
    await _settle()
    second = _call(tmp_path, _change("b.py"))
    await _settle()
    assert [change.label for change, _ in runs] == ["a.py"]
    gate.set()

    assert (await first)[0] == {"for": "a.py"}
    assert (await second)[0] == {"for": "b.py"}
    assert [change.label for change, _ in runs] == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_another_change_past_its_budget_is_busy_and_says_why(
    tmp_path, gated, monkeypatch
) -> None:
    gate, _ = gated
    first = _call(tmp_path, _change("a.py"))
    await _settle()
    monkeypatch.setattr(_test_selection, "SELECTION_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(_test_selection.SelectionUnavailableError) as raised:
        await _test_selection.select_change_tests(tmp_path, None, _change("b.py"))
    assert raised.value.status == "busy"
    assert "another change" in str(raised.value)
    gate.set()
    assert (await first)[0] == {"for": "a.py"}


@pytest.mark.asyncio
async def test_one_caller_timing_out_leaves_the_selection_to_the_other(
    tmp_path, gated, monkeypatch
) -> None:
    gate, runs = gated
    monkeypatch.setattr(_test_selection, "SELECTION_TIMEOUT_SECONDS", 0.01)
    first = _call(tmp_path, _change("a.py"))
    await _settle()
    monkeypatch.setattr(_test_selection, "SELECTION_TIMEOUT_SECONDS", 30.0)
    second = _call(tmp_path, _change("a.py"))

    with pytest.raises(_test_selection.SelectionUnavailableError) as raised:
        await first
    assert raised.value.status == "timeout"
    [(_, cancel)] = runs
    assert not cancel.is_set()
    gate.set()
    assert (await second)[0] == {"for": "a.py"}


@pytest.mark.asyncio
async def test_the_selection_is_cancelled_when_its_last_caller_leaves(
    tmp_path, gated, monkeypatch
) -> None:
    gate, runs = gated
    first, second = _call(tmp_path, _change("a.py")), _call(tmp_path, _change("a.py"))
    await _settle()
    [(_, cancel)] = runs

    first.cancel()
    await _settle()
    assert not cancel.is_set()
    second.cancel()
    await _settle()
    assert cancel.is_set()
    # The same change, still stopping, is waited out and named as such.
    monkeypatch.setattr(_test_selection, "SELECTION_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(_test_selection.SelectionUnavailableError) as raised:
        await _test_selection.select_change_tests(tmp_path, None, _change("a.py"))
    assert raised.value.status == "busy"
    assert "this change, still stopping" in str(raised.value)
    gate.set()
    await _settle()
    assert _test_selection._IN_FLIGHT == {}
