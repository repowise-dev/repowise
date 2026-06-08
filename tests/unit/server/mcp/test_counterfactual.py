"""Per-tool counterfactual estimators — conservative, dict-only, never raise."""

from __future__ import annotations

from repowise.server.mcp_server._savings import counterfactual as cf


def test_get_context_sums_skeleton_full_tokens() -> None:
    result = {
        "targets": {
            "a.py": {"target": "a.py", "skeleton": {"tokens": 200, "full_tokens": 1500}},
            "b.py": {"target": "b.py", "skeleton": {"tokens": 80, "full_tokens": 900}},
            # No skeleton (small-file card / symbol target) → contributes nothing.
            "c.py": {"target": "c.py", "docs": {"summary": "x"}},
        },
        "_meta": {},
    }
    assert cf.replaced_tokens_for("get_context", result) == 2400


def test_get_context_no_skeletons_returns_zero() -> None:
    result = {"targets": {"a.py": {"target": "a.py"}}, "_meta": {}}
    assert cf.replaced_tokens_for("get_context", result) == 0


def test_search_codebase_floors_per_distinct_cited_path() -> None:
    result = {
        "results": [
            {"target_path": "src/a.py"},
            {"target_path": "src/b.py"},
            {"target_path": "src/a.py"},  # duplicate path — counted once
            {"target_path": ""},  # empty path — ignored
            {"title": "no path key"},  # missing path — ignored
        ],
        "_meta": {},
    }
    assert cf.replaced_tokens_for("search_codebase", result) == 2 * cf.SEARCH_FLOOR_PER_HIT


def test_search_codebase_no_results_returns_zero() -> None:
    assert cf.replaced_tokens_for("search_codebase", {"results": [], "_meta": {}}) == 0


def test_unknown_tool_returns_zero() -> None:
    assert cf.replaced_tokens_for("get_risk", {"anything": 1}) == 0


def test_non_dict_result_returns_zero() -> None:
    assert cf.replaced_tokens_for("get_context", None) == 0
    assert cf.replaced_tokens_for("get_context", "oops") == 0


def test_malformed_skeleton_does_not_raise() -> None:
    result = {
        "targets": {
            "a.py": {"skeleton": {"full_tokens": "not-an-int"}},
            "b.py": {"skeleton": "not-a-dict"},
            "c.py": "not-a-dict",
        }
    }
    assert cf.replaced_tokens_for("get_context", result) == 0
