"""Fix first close-out: every item points at a line, and its words read plainly."""

from __future__ import annotations

import re

import pytest

from repowise.core.analysis.health.fix_first import FixFirstQueue, build_fix_first
from repowise.core.analysis.health.queue.eligibility import LOW_VALUE_KINDS
from repowise.core.analysis.health.refactoring.performance_fix import fix_steps
from tests.unit.health.fix_first_rows import FINDINGS, METRICS, _perf

# --- a performance item names the loop it changes -----------------------------------


def _batched(site: dict) -> dict:
    row = _perf("perf3_batch", "src/db.py::fetch")
    row["details"] = {
        **row["details"],
        "plan": {
            "effort_bucket": "M",
            "steps": [
                {"order": 1, "action": "Add a form of load_all that takes every key at once",
                 "symbol": "src/repo.py::load_all", "file_path": "src/repo.py", "line": None,
                 "applicability": "judgment"},
                {"order": 2, "action": "Collect the keys before the loop and call the batched "
                 "form once", "symbol": "sync", "file_path": "src/jobs.py", "line": 40,
                 "applicability": "judgment", **site},
            ],
        },
    }
    return row


def _lead(row: dict, **over):
    return build_fix_first(metrics=[{**METRICS[0], "file_path": "src/repo.py"}],
                           performance=[row], **over).lead


def test_a_perf_item_points_at_the_loop_around_its_lead_call() -> None:
    target = _lead(_batched({"loop_line": 31})).target
    assert (target.file_path, target.line_start, target.symbol) == ("src/jobs.py", 31, "sync")


def test_without_a_stored_loop_it_points_at_the_call() -> None:
    assert _lead(_batched({})).target.line_start == 40


def test_a_call_site_outside_any_function_names_no_symbol() -> None:
    target = _lead(_batched({"symbol": None})).target
    assert (target.file_path, target.line_start, target.symbol) == ("src/jobs.py", 40, None)


def test_a_step_names_its_function_once_and_takes_the_symbol_line() -> None:
    lead = _lead(_batched({}), symbol_lines={"src/repo.py::load_all": 12})
    first, second = lead.action.steps
    assert first.text == "Add a form of load_all that takes every key at once"
    assert first.line == 12
    assert second.text.endswith("(sync)")


def test_a_step_with_no_known_line_keeps_none() -> None:
    assert _lead(_batched({})).action.steps[0].line is None


def test_plan_steps_carry_the_loop_line_of_their_site() -> None:
    steps = fix_steps(
        "batch_or_prefetch_io", "advisory", None, "src/db.py::fetch",
        [{"file_path": "src/jobs.py", "function_name": "sync", "line_start": 40,
          "loop_line": 31}, {"file_path": "src/b.py", "function_name": "g", "line_start": 9}],
    )
    sites = [s for s in steps if s.get("line")]
    assert sites[0]["loop_line"] == 31
    assert "loop_line" not in sites[1]


# --- a long parameter list is a low-value nudge ---------------------------------------


def test_a_long_parameter_list_is_no_candidate() -> None:
    finding = {**FINDINGS[1], "biomarker_type": "primitive_obsession", "public_id": "f_po"}
    queue = build_fix_first(metrics=[METRICS[0]], findings=[finding])
    assert queue.items == ()
    assert queue.totals.excluded["low_value_kind"] == 1
    assert LOW_VALUE_KINDS["primitive_obsession"] == "dev 0/0, all 0/2"


# --- no internal token reaches a text field -------------------------------------------

_RAW = re.compile(
    r"__module__|\bp\d\d\b|percentile|\bNone\b|\(\)|  |:$|:\)|\bn/a\b|[a-z]+-graph|name-match"
    r"|\b(?:entry_reachable|not_entry_reachable|grows_with_data|db)\b"
)


def _texts(item) -> list[str]:
    return [
        item.title, item.why, item.action.summary, item.gain.text, item.effort.basis,
        item.risk.text, item.confidence.reason,
        *(f"{f.label}: {f.value}" for f in item.facts),
        *(s.text for s in item.action.steps),
        *(f"{c.label}: {c.value}" for c in item.context),
        *(f"{r.factor}: {r.value}" for r in item.why_ranked),
        *(t.reason for t in item.verify.tests),
    ]


def _validated(magnitude: str, via: str, dependents: int) -> FixFirstQueue:
    row = _perf("perf3_raw", "src/db.py::fetch")
    details = row["details"]
    row["details"] = {
        **details,
        "facets": {**details["facets"], "loop_magnitude": magnitude},
        "plan": {**details["plan"], "validation": {"tests": ["tests/test_repo.py"], "via": via}},
    }
    finding = {**FINDINGS[1], "public_id": "f_raw"}
    return build_fix_first(
        metrics=[{**METRICS[0], "dependents": dependents}, METRICS[3]],
        findings=[finding], performance=[row],
    )


@pytest.mark.parametrize("via", ["coverage", "call-graph", "import-graph", "name-match", "mixed"])
@pytest.mark.parametrize("magnitude", ["n/a", "grows_with_data", "unknown"])
def test_no_text_field_carries_an_internal_token(via: str, magnitude: str) -> None:
    queue = _validated(magnitude, via, dependents=1)
    assert len(queue.items) == 2
    for item in queue.items:
        for line in _texts(item):
            assert not _RAW.search(line), line


def test_one_importer_reads_in_the_singular() -> None:
    item = next(i for i in _validated("unknown", "mixed", 1).items if i.kind == "finding")
    assert item.risk.text == "Touches 1 file; 1 file imports it."
