"""Fix first's precision levers over plain rows (SPEC_W7 F1-F8 and the lift).

Each lever narrows what may be an item or moves its value; none touches a
score. Each test builds the smallest rows that show the lever firing and the
nearest case where it must not.
"""

from __future__ import annotations

from repowise.core.analysis.health.fix_first import build_fix_first
from tests.unit.health.fix_first_rows import FINDINGS, METRICS

_COMPLEX = FINDINGS[1]  # run in src/core.py: CCN 14, 50 lines, nests 4 deep


def _finding(path: str = "src/core.py", **details) -> dict:
    return {
        **_COMPLEX,
        "file_path": path,
        "public_id": f"finding_{path}",
        "details": {"ccn": 14, "nloc": 50, "max_nesting": 4, "deepest_block": [20, 30],
                    **details},
    }


def _metric(path: str = "src/core.py", **over) -> dict:
    return {**METRICS[0], "file_path": path, **over}


def _queue(findings, metrics=None, **over):
    return build_fix_first(
        metrics=metrics if metrics is not None else [_metric(f["file_path"]) for f in findings],
        findings=findings,
        **over,
    )


# --- F1: code that does not ship -------------------------------------------------


def test_stored_origin_excludes_vendored_docs_generated_and_tooling() -> None:
    origins = ("vendored", "docs_example", "generated", "tooling")
    findings = [_finding(f"src/{o}.py") for o in origins]
    metrics = [_metric(f"src/{o}.py", code_origin=o) for o in origins]
    queue = _queue(findings, metrics)
    assert queue.items == ()
    assert {o: queue.totals.excluded[o] for o in origins} == dict.fromkeys(origins, 1)


def test_production_origin_and_an_unrecorded_one_stay_eligible() -> None:
    findings = [_finding("src/a.py"), _finding("src/b.py")]
    metrics = [_metric("src/a.py", code_origin="production"), _metric("src/b.py")]
    assert len(_queue(findings, metrics).items) == 2


def test_test_origin_is_kept_under_scope_all() -> None:
    findings = [_finding("src/check.py")]
    metrics = [_metric("src/check.py", code_origin="test")]
    assert _queue(findings, metrics).totals.excluded["test"] == 1
    assert len(_queue(findings, metrics, scope="all").items) == 1


def test_a_vendored_directory_counts_as_vendored() -> None:
    queue = _queue([_finding("lib/vendor/x.py")])
    assert queue.totals.excluded["vendored"] == 1


def test_a_deprecated_function_is_no_item() -> None:
    queue = _queue([_finding(deprecated=True)])
    assert queue.items == () and queue.totals.excluded["deprecated"] == 1
    assert len(_queue([_finding()]).items) == 1


# --- F2: one long dispatch on one value -------------------------------------------


def _dry(path: str = "src/core.py", start: int = 25, end: int = 40) -> dict:
    return {"file_path": path, "biomarker_type": "dry_violation", "severity": "medium",
            "line_start": start, "line_end": end, "health_impact": 0.4,
            "public_id": f"finding_dry_{start}", "dimension": "maintainability",
            "details": {"clone_pair_count": 1}}


def test_a_dispatch_function_is_no_candidate() -> None:
    queue = _queue([_finding(dispatch_share=0.8)])
    assert queue.items == () and queue.totals.excluded["inherent_dispatch"] == 1


def test_a_lower_dispatch_share_stays() -> None:
    assert len(_queue([_finding(dispatch_share=0.5)]).items) == 1


def test_a_duplicate_inside_the_dispatch_function_keeps_it() -> None:
    queue = _queue([_finding(dispatch_share=0.8), _dry()])
    assert [i.target.symbol for i in queue.items] == ["run"]
    # A duplicate elsewhere in the file does not, and is never an item itself.
    away = _queue([_finding(dispatch_share=0.8), _dry(start=200, end=220)])
    assert away.items == () and away.totals.excluded["inherent_dispatch"] == 1


def test_the_files_other_findings_still_compete() -> None:
    handler = {**_COMPLEX, "biomarker_type": "error_handling", "function_name": "load",
               "public_id": "finding_eh", "line_start": 70, "line_end": 70,
               "health_impact": 0.5, "details": {}}
    queue = _queue([_finding(dispatch_share=0.9), handler])
    assert [i.target.symbol for i in queue.items] == ["load"]
    assert queue.totals.excluded["inherent_dispatch"] == 0


def test_a_dispatch_function_plan_is_no_candidate() -> None:
    from tests.unit.health.fix_first_rows import PLANS, REFACTORING

    queue = build_fix_first(
        metrics=[_metric()], findings=[_finding(dispatch_share=0.7)],
        refactoring=REFACTORING[:1], plans=PLANS,
    )
    # The plan and then the finding it leaves behind: both on the dispatch.
    assert queue.items == () and queue.totals.excluded["inherent_dispatch"] == 2


# --- F3: small functions --------------------------------------------------------


def test_a_small_simple_function_is_no_candidate() -> None:
    queue = _queue([_finding(ccn=12, nloc=22)])
    assert queue.items == () and queue.totals.excluded["small_function"] == 1


def test_either_floor_keeps_a_function() -> None:
    assert len(_queue([_finding(ccn=12, nloc=30)]).items) == 1
    assert len(_queue([_finding(ccn=15, nloc=12)]).items) == 1


def test_a_finding_with_no_line_count_is_measured_by_its_span() -> None:
    short = {**_finding(ccn=6), "line_start": 10, "line_end": 25}
    del short["details"]["nloc"]
    assert _queue([short]).totals.excluded["small_function"] == 1
    long = {**short, "line_end": 70}
    assert len(_queue([long]).items) == 1


def test_a_small_function_outside_the_size_markers_is_judged_by_its_own_rule() -> None:
    condition = {**_finding(ccn=6, nloc=10), "biomarker_type": "complex_conditional"}
    assert _queue([condition]).totals.excluded["small_function"] == 0
