"""Tests for the FAQ-weighted docs-budget demand miner.

Covers per-file attribution from get_answer / search_codebase results, module
rollup (the attribution the planner budgets on), empty-store passthrough, and
single-module skew.
"""

from __future__ import annotations

import json

from repowise.core.sessions import Event, ToolResult, ToolUse
from repowise.core.sessions.miners.demand import (
    _norm_rel,
    aggregate_file_demand,
    demand_summary_line,
    faq_weighting_enabled,
    mine_events_demand,
    rollup_to_modules,
)

REPO_PREFIX = "c:\\users\\x\\repo"
CWD = "C:\\Users\\x\\repo"


def _answer_call(use_id: str = "a1", name: str = "mcp__repowise__get_answer") -> Event:
    return Event(kind="assistant", cwd=CWD, tool_uses=[ToolUse(id=use_id, name=name, input={})])


def _search_call(use_id: str = "s1", name: str = "mcp__repowise__search_codebase") -> Event:
    return Event(kind="assistant", cwd=CWD, tool_uses=[ToolUse(id=use_id, name=name, input={})])


def _result(use_id: str, payload: dict) -> Event:
    # The demand miner reads the message-level result block content, which the
    # adapter fills from the tool_result block's ``content`` (a JSON string).
    return Event(
        kind="user",
        cwd=CWD,
        tool_results=[ToolResult(tool_use_id=use_id, content=json.dumps(payload))],
    )


# ---------------------------------------------------------------------------
# _norm_rel
# ---------------------------------------------------------------------------


def test_norm_rel_strips_symbol_and_line_range():
    assert _norm_rel("pkg/mod.py::Thing") == "pkg/mod.py"
    assert _norm_rel("pkg/mod.py:10-40") == "pkg/mod.py"
    assert _norm_rel("pkg/mod.py:12") == "pkg/mod.py"
    assert _norm_rel("./pkg/mod.py") == "pkg/mod.py"
    assert _norm_rel("pkg\\mod.py") == "pkg/mod.py"


def test_norm_rel_rejects_non_repo_paths():
    assert _norm_rel("/abs/path.py") is None
    assert _norm_rel("C:/Users/x/repo/f.py") is None  # drive letter -> absolute
    assert _norm_rel("../outside.py") is None
    assert _norm_rel("") is None
    assert _norm_rel(None) is None


# ---------------------------------------------------------------------------
# get_answer attribution
# ---------------------------------------------------------------------------


def test_get_answer_attributes_to_citations():
    events = [
        _answer_call("a1"),
        _result("a1", {"citations": ["pkg/a.py", "pkg/b.py"]}),
    ]
    demand = mine_events_demand(events, REPO_PREFIX)
    assert demand == {"pkg/a.py": 1, "pkg/b.py": 1}


def test_get_answer_reads_nested_result_envelope():
    # Real MCP results wrap the payload under a top-level "result" key.
    events = [
        _answer_call("a1"),
        _result("a1", {"result": {"citations": ["pkg/a.py"], "quotes": [{"path": "pkg/c.py"}]}}),
    ]
    demand = mine_events_demand(events, REPO_PREFIX)
    assert demand == {"pkg/a.py": 1, "pkg/c.py": 1}


def test_get_answer_dedups_and_caps_per_call():
    # Same file cited many ways counts once; >5 distinct files caps at 5.
    payload = {
        "citations": ["pkg/a.py", "pkg/a.py"],
        "best_guesses": [{"file": f"pkg/f{i}.py"} for i in range(10)],
    }
    events = [_answer_call("a1"), _result("a1", payload)]
    demand = mine_events_demand(events, REPO_PREFIX)
    assert demand["pkg/a.py"] == 1
    assert sum(demand.values()) == 5  # per-call cap


# ---------------------------------------------------------------------------
# search_codebase attribution
# ---------------------------------------------------------------------------


def test_search_attributes_to_hit_files_and_symbols():
    payload = {
        "results": [
            {"file": "pkg/a.py"},
            {"target_path": "pkg/b.py"},
            {"symbol_id": "pkg/c.py::Thing"},
        ]
    }
    events = [_search_call("s1"), _result("s1", payload)]
    demand = mine_events_demand(events, REPO_PREFIX)
    assert demand == {"pkg/a.py": 1, "pkg/b.py": 1, "pkg/c.py": 1}


def test_two_calls_accumulate():
    events = [
        _answer_call("a1"),
        _result("a1", {"citations": ["pkg/a.py"]}),
        _search_call("s1"),
        _result("s1", {"results": [{"file": "pkg/a.py"}]}),
    ]
    demand = mine_events_demand(events, REPO_PREFIX)
    assert demand == {"pkg/a.py": 2}


# ---------------------------------------------------------------------------
# scoping + robustness
# ---------------------------------------------------------------------------


def test_events_outside_repo_prefix_are_skipped():
    other = Event(
        kind="assistant",
        cwd="C:\\Users\\x\\other-repo",
        tool_uses=[ToolUse(id="a1", name="mcp__repowise__get_answer", input={})],
    )
    result = Event(
        kind="user",
        cwd="C:\\Users\\x\\other-repo",
        tool_results=[
            ToolResult(tool_use_id="a1", content=json.dumps({"citations": ["pkg/a.py"]}))
        ],
    )
    assert mine_events_demand([other, result], REPO_PREFIX) == {}


def test_empty_store_yields_empty():
    assert mine_events_demand([], REPO_PREFIX) == {}


def test_unpaired_result_ignored():
    events = [_result("orphan", {"citations": ["pkg/a.py"]})]
    assert mine_events_demand(events, REPO_PREFIX) == {}


def test_non_repowise_tool_ignored():
    events = [
        Event(kind="assistant", cwd=CWD, tool_uses=[ToolUse(id="g1", name="Grep", input={})]),
        _result("g1", {"citations": ["pkg/a.py"]}),
    ]
    assert mine_events_demand(events, REPO_PREFIX) == {}


# ---------------------------------------------------------------------------
# module rollup (the attribution the planner budgets on)
# ---------------------------------------------------------------------------


def test_rollup_to_modules_with_dict_map():
    file_demand = {"pkg/a/x.py": 3, "pkg/a/y.py": 2, "pkg/b/z.py": 5}
    mapping = {"pkg/a/x.py": "pkg/a", "pkg/a/y.py": "pkg/a", "pkg/b/z.py": "pkg/b"}
    assert rollup_to_modules(file_demand, mapping) == {"pkg/a": 5, "pkg/b": 5}


def test_rollup_to_modules_with_callable():
    file_demand = {"pkg/a/x.py": 3, "pkg/b/z.py": 5}

    def module_of(path: str) -> str:
        return path.rsplit("/", 1)[0]

    assert rollup_to_modules(file_demand, module_of) == {"pkg/a": 3, "pkg/b": 5}


def test_rollup_drops_unmapped_files():
    file_demand = {"pkg/a/x.py": 3, "gone/old.py": 9}
    mapping = {"pkg/a/x.py": "pkg/a"}  # gone/old.py -> None
    assert rollup_to_modules(file_demand, mapping) == {"pkg/a": 3}


def test_rollup_empty_in_empty_out():
    assert rollup_to_modules({}, {}) == {}


def test_single_module_skew_rolls_up_cleanly():
    # All demand concentrated in one module: the exact shape the gate found.
    file_demand = {f"hot/f{i}.py": 10 for i in range(4)}
    file_demand["cold/f.py"] = 1
    mapping = {p: p.split("/")[0] for p in file_demand}
    modules = rollup_to_modules(file_demand, mapping)
    assert modules == {"hot": 40, "cold": 1}


# ---------------------------------------------------------------------------
# config gate + aggregate entry point
# ---------------------------------------------------------------------------


def test_faq_weighting_enabled_default_on():
    assert faq_weighting_enabled(None) is True
    assert faq_weighting_enabled({}) is True
    assert faq_weighting_enabled({"generation": {"faq_weighting": True}}) is True


def test_faq_weighting_kill_switch():
    assert faq_weighting_enabled({"generation": {"faq_weighting": False}}) is False


def test_aggregate_disabled_returns_empty(tmp_path):
    # Kill switch short-circuits before any transcript I/O.
    out = aggregate_file_demand(tmp_path, repo_config={"generation": {"faq_weighting": False}})
    assert out == {}


def test_aggregate_no_transcripts_returns_empty(tmp_path):
    # No transcript dir for this repo -> empty, no error (fresh install).
    out = aggregate_file_demand(tmp_path, projects_root=tmp_path / "nope")
    assert out == {}


# ---------------------------------------------------------------------------
# summary line (the CLI first-impression surface)
# ---------------------------------------------------------------------------


def test_summary_line_none_when_no_demand():
    assert demand_summary_line({}) is None


def test_summary_line_reports_exact_counts():
    line = demand_summary_line({"a.py": 3, "b.py": 2})
    assert "5 questions" in line
    assert "2 files" in line


def test_summary_line_singular_grammar():
    line = demand_summary_line({"a.py": 1})
    assert "1 question " in line
    assert "1 file " in line
