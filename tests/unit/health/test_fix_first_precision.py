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
