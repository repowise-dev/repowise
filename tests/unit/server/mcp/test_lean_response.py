"""The delivered MCP shape carries only fields an agent can act on."""

from __future__ import annotations

from typing import Any

import pytest

from repowise.server.mcp_server import tool_middleware
from repowise.server.mcp_server._meta import DEBUG_META_ENV, lean_response


def test_empty_and_none_values_are_not_sent_below_the_top_level() -> None:
    result = lean_response(
        {
            "results": [],
            "card": {"context": "", "lineage": [], "extra": {}, "note": None, "kept": 0},
            "_meta": {},
        }
    )

    assert result == {"results": [], "card": {"kept": 0}, "_meta": {}}


def test_layer_debug_stamps_are_not_sent() -> None:
    meta = {"timing_ms": 4.2, "replaced_tokens": 900, "tokens_saved": 700, "kept": 1}

    assert lean_response({"_meta": meta})["_meta"] == {"kept": 1}


def test_an_empty_list_with_a_basis_is_the_answer_and_stays() -> None:
    card = lean_response({"t": {"callers": [], "callers_basis": "python"}})["t"]

    assert card == {"callers": [], "callers_basis": "python"}


def test_false_is_dropped_only_where_it_is_a_status_flag() -> None:
    card = lean_response(
        {
            "t": {
                "commit_count_capped": False,
                "co_change_partners_truncated": False,
                "truncated": False,
                "test_gap": False,
                "verified": False,
            }
        }
    )["t"]

    assert card == {"test_gap": False, "verified": False}


def test_one_truncation_vocabulary_on_a_cut_list() -> None:
    card = lean_response(
        {
            "t": {
                "partners": [1, 2],
                "partners_total": 5,
                "partners_emitted": 2,
                "partners_truncated": True,
                "partners_omitted": 3,
                "partners_reduced_reason": "construction_cap",
            }
        }
    )["t"]

    assert card == {"partners": [1, 2], "partners_total": 5}


def test_counts_of_a_whole_or_projected_away_list_are_not_sent() -> None:
    result = lean_response(
        {
            "files": [1, 2],
            "files_total": 2,
            "files_emitted": 2,
            "fallback_targets_total": 5,
            "fallback_targets_emitted": 0,
            "fallback_targets_reduced_reason": "projection",
            "always_run_total": 69,
        }
    )

    assert result == {"files": [1, 2], "always_run_total": 69}


def test_meta_keeps_the_directive_and_drops_its_diagnostics() -> None:
    result = lean_response(
        {
            "_meta": {
                "index_age_days": 0,
                "indexed_commit": "abc",
                "live_head": "def",
                "index_behind": True,
                "stale_warning": "run `repowise update`",
                "completeness": {"capped": True},
                "state": {"truncated": True},
                "floor": ["dependents_count"],
                "omitted": {"refs": ["repowise#1"], "restore": "expand"},
            },
            "omission_marker": "[repowise#1: 3 lines omitted]",
        }
    )

    assert result == {
        "_meta": {
            "stale_warning": "run `repowise update`",
            "omitted": {"refs": ["repowise#1"], "restore": "expand"},
        }
    }


def test_degradation_is_one_list_of_reasons() -> None:
    meta = lean_response(
        {
            "_meta": {
                "embedder_degraded": False,
                "retrieval_degraded": ["filename", "symbol"],
                "state": {"degraded": True, "degraded_reasons": {"retrieval_degraded": []}},
            }
        }
    )["_meta"]

    assert meta == {"degraded": ["retrieval:filename", "retrieval:symbol"]}


def test_degradation_keeps_every_reason_it_was_given() -> None:
    meta = lean_response(
        {"_meta": {"degraded": "x", "embedder_degraded": True, "synthesis_degraded": "timeout"}}
    )["_meta"]

    assert meta == {"degraded": ["x", "embedder", "synthesis:timeout"]}


def test_a_healthy_embedder_says_nothing() -> None:
    assert lean_response({"_meta": {"embedder_degraded": False}}) == {"_meta": {}}


async def _tool() -> dict[str, Any]:
    return {"rows": [], "flag_truncated": False, "_meta": {"index_behind": False}}


async def test_the_middleware_serves_the_lean_shape() -> None:
    result = await tool_middleware(_tool)()

    assert result == {"rows": [], "_meta": {}}


async def test_debug_meta_keeps_the_whole_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEBUG_META_ENV, "1")

    result = await tool_middleware(_tool)()

    assert result["flag_truncated"] is False
    assert result["_meta"]["index_behind"] is False


async def test_a_full_meta_tool_still_carries_one_omission_entry() -> None:
    """get_overview keeps its whole envelope, but not a marker restating ``_meta.omitted``."""

    async def get_overview(include: list[str] | None = None) -> dict[str, Any]:
        return _with_marker()

    result = await tool_middleware(get_overview)()

    assert "omission_marker" not in result
    assert result["_meta"]["omitted"]["refs"] == ["repowise#aaaaaaaaaaaa"]


def _with_marker() -> dict[str, Any]:
    return {
        "summary": "x",
        "omission_marker": "[repowise#aaaaaaaaaaaa: 3 lines omitted]",
        "_meta": {"omitted": {"refs": ["repowise#aaaaaaaaaaaa"], "restore": "r"}},
    }


def test_a_bridged_cli_call_keeps_its_marker() -> None:
    """CLI commands budget with an empty signature and print the marker themselves."""
    import inspect

    from repowise.server.mcp_server._budget import enforce_response_budget

    def _bridged() -> None:
        pass

    def _tool(question: str = "") -> None:
        pass

    cli = enforce_response_budget("get_overview", _with_marker(), signature=inspect.signature(_bridged), args=(), kwargs={})
    mcp = enforce_response_budget("get_overview", _with_marker(), signature=inspect.signature(_tool), args=(), kwargs={})

    assert "omission_marker" in cli
    assert "omission_marker" not in mcp


async def get_overview() -> dict[str, Any]:
    return {"_meta": {"index_behind": False, "embedder_degraded": False, "contract_version": 4}}


async def test_the_orientation_call_keeps_diagnostics_but_no_false_status_flag() -> None:
    meta = (await tool_middleware(get_overview)())["_meta"]

    assert "embedder_degraded" not in meta
    assert meta["index_behind"] is False
    assert meta["contract_version"] == 4


async def test_the_orientation_call_keeps_a_raised_flag() -> None:
    async def get_overview() -> dict[str, Any]:
        return {"_meta": {"embedder_degraded": True}}

    assert (await tool_middleware(get_overview)())["_meta"]["embedder_degraded"] is True


async def test_debug_meta_keeps_the_orientation_calls_false_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DEBUG_META_ENV, "1")

    meta = (await tool_middleware(get_overview)())["_meta"]

    assert meta["embedder_degraded"] is False
