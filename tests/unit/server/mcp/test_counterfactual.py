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
    assert cf.replaced_tokens_for("list_repos", {"anything": 1}) == 0


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


def test_get_answer_counts_search_plus_cited_reads() -> None:
    result = {
        "answer": "The default is 2 (pkg/a.py).",
        "citations": ["pkg/a.py"],
        "fallback_targets": ["pkg/a.py", "pkg/b.py"],
    }
    expected = cf.ANSWER_SEARCH_FLOOR + 2 * cf.ANSWER_READ_FLOOR_PER_FILE
    assert cf.replaced_tokens_for("get_answer", result) == expected


def test_get_answer_empty_answer_claims_nothing() -> None:
    # Gated/low responses tell the agent to go read — those reads still
    # happen, so claiming them as saved repeats the E11 miscalibration.
    result = {"answer": "", "best_guesses": [{"file": "pkg/a.py"}]}
    assert cf.replaced_tokens_for("get_answer", result) == 0


def test_fixed_floor_tools_credit_successful_calls_only() -> None:
    assert cf.replaced_tokens_for("get_risk", {"targets": ["a.py"]}) == cf.RISK_FLOOR
    assert cf.replaced_tokens_for("get_risk", {"error": "nope"}) == 0
    assert cf.replaced_tokens_for("get_why", {"decisions": []}) == cf.WHY_FLOOR
    assert cf.replaced_tokens_for("get_overview", {"architecture": {}}) == cf.OVERVIEW_FLOOR
    assert cf.replaced_tokens_for("get_health", {"summary": {}}) == cf.HEALTH_FLOOR


def test_dead_end_records_debit_row(tmp_path, monkeypatch) -> None:
    """An error response writes raw=0/distilled=N — a negative net at
    aggregation, so the ledger stops only ever crediting (E11)."""
    from repowise.core.distill.store import (
        OMISSIONS_DB_FILENAME,
        OMISSIONS_DIRNAME,
        OmissionStore,
    )
    from repowise.server.mcp_server._savings.recorder import record_mcp_dead_end

    db_dir = tmp_path / ".repowise" / OMISSIONS_DIRNAME
    db_dir.mkdir(parents=True)
    db_path = db_dir / OMISSIONS_DB_FILENAME
    OmissionStore(db_path).close()  # create the sidecar

    assert record_mcp_dead_end(tmp_path, "get_symbol", 350) is True

    store = OmissionStore(db_path)
    try:
        summary = store.savings_summary()
        per = summary["per_filter"]["get_symbol"]
        assert per["raw_tokens"] == 0
        assert per["distilled_tokens"] == 350
        assert per["saved_tokens"] == -350
    finally:
        store.close()
