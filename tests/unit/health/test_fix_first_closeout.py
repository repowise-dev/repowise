"""Fix first close-out: every item points at a line, and its words read plainly."""

from __future__ import annotations

from repowise.core.analysis.health.fix_first import build_fix_first
from repowise.core.analysis.health.fix_first.build import LOW_VALUE_KINDS
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
