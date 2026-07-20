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


def test_get_context_module_credits_per_child_file() -> None:
    # A module card lists child files the agent would otherwise open one by one.
    result = {
        "targets": {
            "src/api": {
                "target": "src/api",
                "type": "module",
                "docs": {"files": [{"path": "src/api/a.py"}, {"path": "src/api/b.py"}]},
            }
        }
    }
    assert cf.replaced_tokens_for("get_context", result) == 2 * cf.CONTEXT_MODULE_FILE_FLOOR


def test_get_context_module_credit_is_capped() -> None:
    # The child-page query is recursive, so a top-level module lists hundreds of
    # files; the agent would never have opened them all. Credit stays capped.
    many = [{"path": f"src/f{i}.py"} for i in range(500)]
    result = {"targets": {"src": {"target": "src", "type": "module", "docs": {"files": many}}}}
    assert cf.replaced_tokens_for("get_context", result) == cf.CONTEXT_MODULE_MAX


def test_get_risk_unions_will_break_and_missing_cochanges() -> None:
    # A file that both breaks and is a missed co-change partner is one file.
    # Two targets so the total clears RISK_FLOOR — otherwise the floor masks
    # whether the overlapping file was counted once or twice.
    result = {
        "targets": {"a.py": {}, "b.py": {}},
        "directive": {"will_break": ["c.py"], "missing_cochanges": ["c.py"]},
    }
    expected = 2 * cf.RISK_PER_TARGET + 1 * cf.RISK_PER_RELATED_FILE
    assert expected > cf.RISK_FLOOR
    assert cf.replaced_tokens_for("get_risk", result) == expected


def test_get_context_symbol_credits_fixed_floor() -> None:
    result = {"targets": {"Foo": {"target": "Foo", "type": "symbol", "docs": {"name": "Foo"}}}}
    assert cf.replaced_tokens_for("get_context", result) == cf.CONTEXT_SYMBOL_FLOOR


def test_get_context_mixed_targets_sum() -> None:
    # Skeleton file + module + symbol all contribute; error target is skipped.
    result = {
        "targets": {
            "a.py": {"target": "a.py", "type": "file", "skeleton": {"full_tokens": 1500}},
            "src/api": {
                "target": "src/api",
                "type": "module",
                "docs": {"files": [{"path": "x.py"}]},
            },
            "Foo": {"target": "Foo", "type": "symbol"},
            "gone.py": {"target": "gone.py", "error": "not found"},
        }
    }
    expected = 1500 + cf.CONTEXT_MODULE_FILE_FLOOR + cf.CONTEXT_SYMBOL_FLOOR
    assert cf.replaced_tokens_for("get_context", result) == expected


def test_get_context_skeleton_file_not_double_counted() -> None:
    # A file target carrying a skeleton counts once (full_tokens), never also
    # as a per-type floor.
    result = {
        "targets": {"a.py": {"target": "a.py", "type": "file", "skeleton": {"full_tokens": 900}}}
    }
    assert cf.replaced_tokens_for("get_context", result) == 900


def test_get_risk_scales_with_targets_and_blast() -> None:
    result = {
        "targets": {"a.py": {}, "b.py": {}},
        "directive": {"will_break": ["c.py"], "missing_cochanges": ["d.py", "e.py"]},
    }
    expected = 2 * cf.RISK_PER_TARGET + 3 * cf.RISK_PER_RELATED_FILE
    assert cf.replaced_tokens_for("get_risk", result) == expected


def test_get_risk_floors_when_signal_thin() -> None:
    assert cf.replaced_tokens_for("get_risk", {"targets": {}}) == cf.RISK_FLOOR
    assert cf.replaced_tokens_for("get_risk", {"error": "nope"}) == 0


def test_get_blast_radius_scales_and_caps() -> None:
    assert cf.replaced_tokens_for("get_blast_radius", {"total_impacted": 3}) == max(
        cf.BLAST_FLOOR, 3 * cf.BLAST_PER_IMPACTED
    )
    # No impact still floors a successful call.
    assert cf.replaced_tokens_for("get_blast_radius", {"total_impacted": 0}) == cf.BLAST_FLOOR
    # Huge fan-out is capped.
    assert cf.replaced_tokens_for("get_blast_radius", {"total_impacted": 10_000}) == cf.BLAST_MAX
    assert cf.replaced_tokens_for("get_blast_radius", {"error": "workspace only"}) == 0


def test_get_execution_flows_scales_with_trace_nodes() -> None:
    # 5 + 3 = 8 nodes, chosen so the scaled value clears FLOWS_FLOOR — otherwise
    # max() collapses and the test would pass with the scaling deleted.
    result = {"flows": [{"trace": [1, 2, 3, 4, 5]}, {"depth": 2}]}
    scaled = 8 * cf.FLOWS_PER_NODE
    assert scaled > cf.FLOWS_FLOOR
    assert cf.replaced_tokens_for("get_execution_flows", result) == scaled
    assert cf.replaced_tokens_for("get_execution_flows", {"flows": []}) == 0
    assert cf.replaced_tokens_for("get_execution_flows", {"error": "no entry"}) == 0


def test_newly_covered_tools_credit_successful_calls() -> None:
    # Item 7: tools that previously fell through and could only ever take a
    # dead-end debit now earn a floor on success.
    assert cf.replaced_tokens_for("get_change_risk", {"summary": "x"}) == cf.CHANGE_RISK_FLOOR
    assert cf.replaced_tokens_for("get_architecture", {"layers": []}) == cf.ARCHITECTURE_FLOOR
    assert cf.replaced_tokens_for("get_dependency_path", {"path": []}) == cf.DEPENDENCY_FLOOR
    assert cf.replaced_tokens_for("get_conformance", {"contracts": []}) == cf.CONFORMANCE_FLOOR
    assert cf.replaced_tokens_for("get_change_risk", {"error": "bad revspec"}) == 0


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
