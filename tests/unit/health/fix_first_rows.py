"""One small repository as plain rows, shared by the Fix-first builder and loader tests."""

from __future__ import annotations

from datetime import datetime

AT = datetime(2026, 9, 30, 12, 0)

METRICS = [
    {"file_path": "src/core.py", "score": 4.0, "nloc": 300, "is_test": False,
     "commit_count_90d": 12, "dependents": 9, "analyzed_commit": "abc", "updated_at": AT},
    {"file_path": "src/plain.py", "score": 7.0, "nloc": 80, "is_test": False,
     "commit_count_90d": 1, "dependents": 1, "analyzed_commit": "abc", "updated_at": AT},
    {"file_path": "src/hist.py", "score": 6.0, "nloc": 50, "is_test": False,
     "commit_count_90d": 3, "dependents": 0, "analyzed_commit": "abc", "updated_at": AT},
    {"file_path": "src/repo.py", "score": 8.0, "nloc": 90, "is_test": False,
     "commit_count_90d": 2, "dependents": 2, "analyzed_commit": "abc", "updated_at": AT},
    {"file_path": "tests/test_core.py", "score": 5.0, "nloc": 90, "is_test": True,
     "commit_count_90d": 4, "dependents": 0, "analyzed_commit": "abc", "updated_at": AT},
    {"file_path": "scripts/tool.py", "score": 5.0, "nloc": 90, "is_test": False,
     "commit_count_90d": 1, "dependents": 0, "analyzed_commit": "abc", "updated_at": AT},
]

FINDINGS = [
    # A history marker outweighs the code-shape finding on the same file.
    {"file_path": "src/core.py", "biomarker_type": "change_entropy", "severity": "high",
     "health_impact": 2.5, "reason": "changes are scattered", "public_id": "finding_h1",
     "dimension": "defect"},
    {"file_path": "src/core.py", "biomarker_type": "complex_method", "severity": "high",
     "function_name": "run", "line_start": 10, "line_end": 60, "health_impact": 1.0,
     "reason": "run has cyclomatic complexity 14", "public_id": "finding_c1",
     "dimension": "defect"},
    {"file_path": "src/plain.py", "biomarker_type": "nested_complexity", "severity": "medium",
     "function_name": "walk", "line_start": 5, "line_end": 40, "health_impact": 0.6,
     "reason": "walk nests 5 levels deep", "public_id": "finding_n1", "dimension": "defect"},
    # Only history: context, never an item.
    {"file_path": "src/hist.py", "biomarker_type": "co_change_scatter", "severity": "high",
     "health_impact": 1.2, "reason": "co-changes with 20 files", "public_id": "finding_h2",
     "dimension": "defect"},
    {"file_path": "tests/test_core.py", "biomarker_type": "large_method", "severity": "high",
     "function_name": "test_all", "line_start": 1, "line_end": 90, "health_impact": 0.5,
     "reason": "test_all is 90 lines long", "public_id": "finding_t1", "dimension": "defect"},
    {"file_path": "src/plain.py", "biomarker_type": "complex_method", "severity": "high",
     "function_name": "gone", "health_impact": 3.0, "public_id": "finding_r1",
     "dimension": "defect", "status": "resolved"},
]


def _step(plan_id: str, kind: str, symbol: str, impact: float, *, mechanical: bool) -> dict:
    return {
        "plan_id": plan_id, "refactoring_type": kind, "target_symbol": symbol,
        "file_path": "src/core.py", "line_start": 10, "line_end": 60, "impact_delta": impact,
        "applicability": {"classification": "mechanical" if mechanical else "judgment"},
        "validation_profile_id": "validation_1",
    }


REFACTORING = [
    {"opportunity_id": "refop2_core", "rank_position": 0, "rank_score": 2.0,
     "file_path": "src/core.py", "lead_biomarker": "complex_method",
     "lead_refactoring_type": "extract_method", "effort_bucket": "S", "confidence": "high",
     "affected_files_total": 1, "recoverable_health": 2.0,
     "details": {
         "steps": [_step("refac2_big", "extract_method", "run", 1.6, mechanical=True),
                   _step("refac2_tiny", "extract_method", "run", 0.2, mechanical=True)],
         "validation_profiles": [{
             "id": "validation_1", "basis": "inferred", "via": "call-graph", "total": 7,
             "tests": [f"tests/test_core_{i}.py" for i in range(7)],
             "commands": ["pytest tests/test_core_0.py"],
         }],
         "dependents": 9, "lead_finding_ids": ["finding_c1"],
     }},
    # Under the minimum worth: counted, never read further.
    {"opportunity_id": "refop2_small", "rank_position": 1, "rank_score": 0.3,
     "file_path": "src/repo.py", "lead_biomarker": "large_method",
     "lead_refactoring_type": "extract_method", "effort_bucket": "S", "confidence": "high",
     "affected_files_total": 1, "recoverable_health": 0.3, "details": {"steps": []}},
    {"opportunity_id": "refop2_test", "rank_position": 2, "rank_score": 1.0,
     "file_path": "tests/test_core.py", "lead_biomarker": "large_method",
     "lead_refactoring_type": "extract_method", "effort_bucket": "S", "confidence": "high",
     "affected_files_total": 1, "recoverable_health": 1.0,
     "details": {"steps": [{**_step("refac2_t", "extract_method", "test_all", 1.0,
                                    mechanical=False), "file_path": "tests/test_core.py"}]}},
]

PLANS = [
    {"public_id": "refac2_big", "refactoring_type": "extract_method",
     "evidence": {"slice_nloc": 12, "ccn_removed": 4}, "plan": {"span": {"start": 20, "end": 35}}},
    {"public_id": "refac2_tiny", "refactoring_type": "extract_method",
     "evidence": {"slice_nloc": 2, "ccn_removed": 1}, "plan": {"span": {"start": 40, "end": 41}}},
]


def _perf(opp: str, sink: str, **over) -> dict:
    row = {
        "opportunity_id": opp, "rank_position": 0, "rank_score": 20,
        "execution_context": "production", "boundary_kind": "db", "biomarker_type": "io_in_loop",
        "actionability_state": "advisory", "plan_state": "available",
        "fix_strategy": "batch_or_prefetch_io", "fix_safety": "advisory",
        "file_path": "src/repo.py", "intervention_symbol": "src/repo.py::load_all",
        "terminal_sink": sink, "affected_call_sites_total": 3, "affected_files_total": 2,
        "details": {
            "facets": {"exposure": "entry_reachable", "loop_magnitude": "grows_with_data",
                       "amplification": "per_iteration", "actionability_confidence": "medium"},
            "plan": {"effort_bucket": "M", "steps": [
                {"order": 1, "action": "Collect the keys before the loop", "symbol": "load_all",
                 "file_path": "src/repo.py", "line": 12, "applicability": "judgment"}]},
            "fix_rationale": "The shared sink is proven.",
        },
    }
    return {**row, **over}


PERFORMANCE = [
    # One loop, two sinks: one item.
    _perf("perf2_a", "src/db.py::fetch"),
    _perf("perf2_b", "src/db.py::save", rank_position=3),
    _perf("perf2_expected", "x", intervention_symbol="src/repo.py::other",
          actionability_state="expected", plan_state="no_safe_plan", fix_strategy=None,
          rank_position=5),
    _perf("perf2_noplan", "y", intervention_symbol="src/repo.py::third",
          actionability_state="investigate", plan_state="no_safe_plan", fix_strategy=None,
          rank_position=6),
    _perf("perf2_tool", "z", file_path="scripts/tool.py",
          intervention_symbol="scripts/tool.py::main", execution_context="tooling",
          rank_position=7),
]
