"""The Fix-first builder over plain rows: eligibility, ranking and identity."""

from __future__ import annotations

import copy

import pytest

from repowise.core.analysis.health.fix_first import FIX_EXCLUSIONS, build_fix_first
from repowise.core.analysis.health.refactoring.recipe import ELSEWHERE_NOTE
from tests.unit.health.fix_first_rows import (
    FINDINGS,
    METRICS,
    PERFORMANCE,
    PLANS,
    REFACTORING,
    _perf,
)


def _build(**over):
    rows = {
        "metrics": METRICS,
        "findings": FINDINGS,
        "refactoring": REFACTORING,
        "performance": PERFORMANCE,
        "plans": PLANS,
    }
    return build_fix_first(**{**rows, **over})


def test_empty_repository_has_no_lead() -> None:
    queue = build_fix_first()
    assert queue.items == () and queue.lead is None
    assert queue.totals.candidates == 0
    assert queue.as_dict()["lead"] is None


def test_each_exclusion_is_counted_by_reason() -> None:
    queue = _build()
    assert set(queue.totals.excluded) == set(FIX_EXCLUSIONS)
    assert queue.totals.excluded == {
        # The test file's plan and its own findings are two candidates.
        "test": 2,
        "tooling": 1,
        "unknown": 0,
        "generated": 0,
        "expected": 1,
        # An investigate cause has no strategy: the perf default queue's reason.
        "no_strategy": 1,
        "no_plan": 0,
        "below_min_worth": 1,
        "history_only": 1,
        "vendored": 0,
        "docs_example": 0,
        "deprecated": 0,
        "gated_off": 0,
        "cold_path": 0,
        "unreachable": 0,
        "inherent_dispatch": 0,
        "small_function": 0,
        "no_concrete_step": 0,
        "low_value_kind": 0,
        "kind_unaudited": 0,
        "terminal_flow": 0,
        "moderate_quiet": 0,
        "unmeasured_cost": 0,
        "cold_role": 0,
        "background_unproven": 0,
        "unconfirmed_cost": 0,
    }
    assert queue.totals.dormant == 0
    assert queue.totals.eligible == 3
    assert queue.totals.candidates == 3 + 7
    assert queue.by_improves == {"defect": 2, "maintainability": 0, "performance": 1}


def test_history_never_leads_and_rides_along_as_context() -> None:
    queue = _build()
    # The production, entry-reachable, growing database loop shares the top
    # band with the refactor and leads it on score.
    assert [i.target.file_path for i in queue.items] == [
        "src/repo.py",
        "src/core.py",
        "src/plain.py",
    ]
    core = queue.items[1]
    assert core.kind == "refactor"
    assert "change" not in core.title.lower() and "entropy" not in core.why
    assert [(c.label, c.value) for c in core.context] == [
        ("recent changes", "changed 12 times in 90 days"),
        ("scattered changes", "its changes are spread across many unrelated commits"),
    ]
    # The history-only file is no item at all.
    assert all(i.target.file_path != "src/hist.py" for i in queue.items)


def test_text_quotes_the_stored_numbers() -> None:
    core = next(i for i in _build().items if i.kind == "refactor")
    assert core.title == (
        "Start breaking up run (CCN 44, 50 lines): first lift lines 20-35"
    )
    # The size is a fact; the why says why it matters here.
    assert core.why == (
        "run has many paths; easier to read and change once split; "
        "its file is imported by 9 files, changed 12 times in 90 days."
    )
    assert ("size", "CCN 44, 50 lines, nests 4 deep") in [(f.label, f.value) for f in core.facts]
    assert core.action.steps[0].text == "Extract lines 20-35 of run into sum_rows(rows, limit) -> total"
    assert core.action.steps[1].text == (
        "Extract lines 40-41 of run into <name>(); name it for what the lines do"
    )
    # The model's credit is the gain, with one decimal; the builder does not re-judge it.
    assert (core.gain.value, core.gain.text) == (2.0, "+2.0 health on this file")
    assert core.source.plan_ids == ("refac2_big", "refac2_tiny")
    assert len(core.verify.tests) == 5 and core.verify.tests_total == 7
    assert core.verify.command == "pytest tests/test_core_0.py"


def test_an_excluded_plan_leaves_the_files_findings_to_compete() -> None:
    """Every candidate lands in exactly one bucket, whatever the other one decided."""
    small = {**REFACTORING[1], "opportunity_id": "refop2_plain", "file_path": "src/plain.py"}
    queue = _build(refactoring=[small], performance=[])
    plain = [i for i in queue.items if i.target.file_path == "src/plain.py"]
    assert [i.kind for i in plain] == ["finding"]
    assert queue.totals.excluded["below_min_worth"] == 1
    assert queue.totals.candidates == queue.totals.eligible + sum(queue.totals.excluded.values())


def test_tiers_follow_value_confidence_and_readiness() -> None:
    by_kind = {i.kind: i for i in _build().items}
    assert by_kind["refactor"].tier == "now"
    assert by_kind["perf_fix"].tier == "now"  # a plan-ready cause with a proven plan
    assert by_kind["finding"].tier == "later"
    assert all(i.why_ranked[-1].factor == "tier" for i in by_kind.values())


def test_perf_rows_group_by_intervention() -> None:
    perf = next(i for i in _build().items if i.kind == "perf_fix")
    assert perf.source.opportunity_id == "perf2_a"
    assert ("sinks this fix covers", "2") in [(f.label, f.value) for f in perf.facts]
    assert perf.gain.text == "one database call per loop iteration, grows with the data"
    assert perf.title == "Batch the database calls loops make through load_all"


def test_ids_are_stable_and_independent_of_rank() -> None:
    first = _build()
    moved = copy.deepcopy(REFACTORING)
    moved[0]["rank_position"] = 9
    second = _build(refactoring=moved)
    assert [i.id for i in first.items] == [i.id for i in second.items]
    assert all(i.id.startswith("fix1_") and len(i.id) == 25 for i in first.items)
    assert [i.rank for i in first.items] == [0, 1, 2]


def test_no_kind_takes_more_than_three_of_the_first_five() -> None:
    refactors = []
    for n in range(5):
        row = copy.deepcopy(REFACTORING[0])
        row["opportunity_id"] = f"refop2_{n}"
        row["file_path"] = f"src/core{n}.py"
        row["details"]["steps"] = row["details"]["steps"][:1]
        refactors.append(row)
    # A loop that grows, reached from an entry point, but no request, message
    # or job is known to run: worth doing, ranked below the refactors' value.
    perf = _perf("perf2_a", "s", execution_role="unknown")
    perf["details"] = {**perf["details"], "facets": {
        "loop_magnitude": "grows_with_data", "growth_source": "loop",
        "exposure": "entry_reachable",
    }}
    queue = _build(refactoring=refactors, performance=[perf], findings=[])
    # The fourth place goes to the perf fix; the fifth back to a refactor,
    # since no other kind has anything left at value 2 or above.
    assert [i.kind for i in queue.items[:5]] == [*["refactor"] * 3, "perf_fix", "refactor"]


def test_scope_all_keeps_tests_and_labels_them() -> None:
    queue = _build(scope="all")
    test_item = next(i for i in queue.items if i.target.file_path == "tests/test_core.py")
    assert ("file kind", "test") in [(c.label, c.value) for c in test_item.context]
    assert queue.totals.excluded["test"] == 0


def test_limit_caps_items_not_totals() -> None:
    queue = _build(limit=1)
    assert len(queue.items) == 1 and queue.totals.shown == 1 and queue.totals.eligible == 3
    assert queue.find(queue.lead.id) is queue.lead


def test_titles_and_whys_carry_no_biomarker_ids() -> None:
    from repowise.core.analysis.health.scoring import _BIOMARKER_CATEGORY

    for item in _build().items:
        for text in (item.title, item.why, item.gain.text):
            assert not any(marker in text for marker in _BIOMARKER_CATEGORY), text
        assert len(item.title) <= 90


def test_cli_code_ships_for_code_shape_but_not_for_performance() -> None:
    from repowise.core.analysis.health.perf.causal import code_context, execution_context

    path = "packages/cli/src/app/cli/commands/update.py"
    assert (code_context(path), execution_context(path)) == ("production", "tooling")
    assert code_context("scripts/run.py") == execution_context("scripts/run.py") == "tooling"
    cli = {**METRICS[0], "file_path": path}
    finding = {**FINDINGS[1], "file_path": path}
    queue = _build(metrics=[cli], findings=[finding], refactoring=[], performance=[])
    assert [i.target.file_path for i in queue.items] == [path]


def test_a_production_file_under_a_samples_package_is_eligible() -> None:
    path = "src/main/java/org/springframework/samples/petclinic/owner/PetController.java"
    row = {**METRICS[0], "file_path": path, "code_origin": "production"}
    finding = {**FINDINGS[1], "file_path": path}
    queue = _build(metrics=[row], findings=[finding], refactoring=[], performance=[])
    assert queue.totals.eligible > 0
    assert [i.target.file_path for i in queue.items] == [path]


def test_value_leads_tier_and_a_huge_function_says_so() -> None:
    """A function far past every bar outranks tidy work that is safer to start."""
    huge = {**FINDINGS[1], "file_path": "src/plain.py", "function_name": "walk",
            "public_id": "finding_huge", "details": {"ccn": 249, "nloc": 1280, "max_nesting": 5}}
    queue = _build(findings=[*FINDINGS, huge], performance=[])
    lead = queue.lead
    assert (lead.kind, lead.tier, lead.target.file_path) == ("finding", "next", "src/plain.py")
    assert lead.title == "Break up walk (CCN 249, 1,280 lines)"
    assert ("problem size", "4") in [(f.factor, f.value) for f in lead.why_ranked]
    # With a plan, the title names the problem and the first concrete step.
    big = {**FINDINGS[1], "details": {"ccn": 120, "nloc": 900, "max_nesting": 6}}
    planned = _build(findings=[big], performance=[]).lead
    assert planned.title == (
        "Start breaking up run (CCN 120, 900 lines): first lift lines 20-35"
    )


def test_every_open_refactoring_opportunity_is_judged() -> None:
    judged: dict = {}
    queue = _build(limit=0, judged=judged)
    assert queue.items == ()
    assert {k: j.reason for k, j in judged.items()} == {
        "refop2_core": None,
        "refop2_small": "below_min_worth",
        "refop2_test": "test",
    }
    # An eligible plan is valued and tiered as its item is; an excluded one is not.
    full = _build(limit=None)
    item = next(i for i in full.items if i.source.opportunity_id == "refop2_core")
    assert judged["refop2_core"].tier == item.tier and judged["refop2_core"].value is not None
    assert judged["refop2_small"].value is None and judged["refop2_small"].tier is None


def test_a_finding_item_takes_its_tests_from_the_validate_callback() -> None:
    asked: list[tuple] = []

    def validate(path, function, start, end):
        asked.append((path, function, start, end))
        return {
            "basis": "inferred",
            "via": "call-graph",
            "total": 1,
            "tests": ["tests/test_plain.py::test_walk"],
            "commands": ["pytest tests/test_plain.py::test_walk"],
        }

    queue = build_fix_first(
        metrics=METRICS,
        findings=FINDINGS,
        refactoring=REFACTORING,
        performance=PERFORMANCE,
        plans=PLANS,
        validate=validate,
    )
    walk = next(i for i in queue.items if i.target.file_path == "src/plain.py")
    assert asked == [("src/plain.py", "walk", 5, 40)]
    assert walk.verify.basis == "inferred"
    assert [t.path for t in walk.verify.tests] == ["tests/test_plain.py::test_walk"]
    assert "call graph" in walk.verify.tests[0].reason


def test_fix_first_counts_only_in_its_own_reasons() -> None:
    """The shared tally takes any reason, so a ladder that grew one Fix first
    does not report would surface here, not on the wire."""
    moved = {**REFACTORING[0], "opportunity_id": "refop2_move", "file_path": "src/move.py",
             "details": {"steps": [_step_kind("move_method")]}}
    rows = {
        "refactoring": [*REFACTORING, moved],
        "performance": [
            *PERFORMANCE,
            _perf("perf2_cold", "c", intervention_symbol="src/repo.py::boot",
                  actionability_state="expected", actionability_reason="cold_path"),
            _perf("perf2_unmeasured", "u", intervention_symbol="src/repo.py::guess",
                  details={"facets": {"loop_magnitude": "unknown"}}),
        ],
    }
    for scope in ("production", "all"):
        queue = _build(scope=scope, **rows)
        assert set(queue.totals.excluded) == set(FIX_EXCLUSIONS)
        assert queue.totals.excluded["kind_unaudited"] == 1


def _step_kind(kind: str) -> dict:
    return {"plan_id": "refac2_mv", "refactoring_type": kind, "target_symbol": "src/move.py::run",
            "file_path": "src/move.py", "line_start": 5}


def test_a_due_item_carries_its_first_step_and_verify_inline() -> None:
    by_kind = {i.kind: i for i in _build().items}
    refactor, perf, finding = by_kind["refactor"], by_kind["perf_fix"], by_kind["finding"]
    assert refactor.compact()["first_step"] == {
        "action": "Extract lines 20-35 of run into sum_rows(rows, limit) -> total",
        "line": 20,
    }
    assert refactor.compact()["verify"] == {"command": "pytest tests/test_core_0.py"}
    assert perf.compact()["first_step"] == {
        "action": "Collect the keys before the loop (load_all)",
        "line": 12,
    }
    assert "verify" not in perf.compact()  # the plan names no command
    # A later item is a list row only; its plan is one lookup away.
    assert finding.tier == "later"
    assert not {"first_step", "verify"} & set(finding.compact())


def test_a_step_names_its_own_command_only_when_it_differs() -> None:
    perf = _perf("perf2_a", "s")
    perf["details"]["plan"] = {
        **perf["details"]["plan"],
        "validation": {"basis": "inferred", "total": 2, "tests": ["tests/test_a.py"],
                       "commands": ["pytest tests/test_a.py"]},
        "steps": [
            {"order": 1, "action": "Collect the keys before the loop", "file_path": "src/repo.py",
             "line": 12, "applicability": "judgment",
             "verify": {"commands": ["pytest tests/test_b.py"], "tests": ["tests/test_b.py"],
                        "coverage": "inferred"}},
            {"order": 2, "action": "Fetch them in one call", "file_path": "src/repo.py",
             "line": 14, "applicability": "judgment"},
        ],
    }
    item = next(i for i in _build(performance=[perf]).items if i.kind == "perf_fix")
    assert item.verify.command == "pytest tests/test_a.py"
    assert [s.command for s in item.action.steps] == ["pytest tests/test_b.py", None]
    # Inline, the item's command: a step's own, narrower one stays on the step.
    assert item.compact()["verify"] == {"command": "pytest tests/test_a.py"}


def test_an_extract_method_step_carries_the_helper_header_and_call() -> None:
    plans = copy.deepcopy(PLANS)
    plans[0]["plan"]["new_symbol"] = {"signature_text": "def _sum_rows(rows, limit):"}
    plans[0]["plan"]["call_site"] = {"replace_span": {"start": 20, "end": 35},
                                     "new_text": "total = _sum_rows(rows, limit)"}
    core = next(i for i in _build(plans=plans).items if i.kind == "refactor")
    first, second = core.action.steps
    assert (first.signature, first.call) == ("def _sum_rows(rows, limit):",
                                             "total = _sum_rows(rows, limit)")
    assert (second.signature, second.call) == (None, None)
    # The header is on the step, in the full item; the compact row names the edit only.
    assert set(core.compact()["first_step"]) == {"action", "line"}
    # A finding with no plan of its own takes the same texts from the span it starts at.
    plans[0].update(file_path="src/core.py", target_symbol="src/core.py::run")
    lone = _build(plans=plans, refactoring=[], performance=[]).lead
    assert lone.action.steps[0].signature == "def _sum_rows(rows, limit):"


def test_an_unnamed_helper_is_left_out_of_titles() -> None:
    """A title never reads "<name>": the step already says to name the helper."""
    plans = copy.deepcopy(PLANS)
    del plans[0]["plan"]["suggested_name"]
    core = next(i for i in _build(plans=plans).items if i.kind == "refactor")
    assert core.title == "Start breaking up run (CCN 44, 50 lines): first lift lines 20-35"
    small = [{**FINDINGS[1], "details": {"ccn": 12, "nloc": 50}}]
    row = copy.deepcopy(REFACTORING[0])
    row["details"]["steps"] = row["details"]["steps"][:1]
    core = next(i for i in _build(plans=plans, findings=small, refactoring=[row]).items
                if i.kind == "refactor")
    assert core.title == "Extract lines 20-35 of run"


def test_an_item_no_test_reaches_says_to_pin_its_behaviour_first() -> None:
    first = "No test reaches this; add a characterization test for `run` before the edit."
    row = copy.deepcopy(REFACTORING[0])
    row["details"]["validation_profiles"] = [
        {"id": "validation_1", "basis": "unknown", "total": 0, "tests": [], "commands": [],
         "prerequisite": first}
    ]
    core = next(i for i in _build(refactoring=[row]).items if i.kind == "refactor")
    assert (core.verify.tests, core.verify.command, core.verify.prerequisite) == ((), None, first)
    assert core.compact()["verify"] == {"prerequisite": first}
    # A finding the validate callback found no test for takes the same step.
    queue = _build(validate=lambda *_: {"prerequisite": first})
    walk = next(i for i in queue.items if i.target.file_path == "src/plain.py")
    assert (walk.verify.basis, walk.verify.prerequisite) == ("unknown", first)


def _staged_plans(count: int = 3, orchestrator: dict | None = None) -> list:
    plans = copy.deepcopy(PLANS)
    stage = {k: v for k, v in plans[0]["plan"].items() if k != "stages"}
    plans[0]["plan"]["stages"] = [copy.deepcopy(stage) for _ in range(count)]
    plans[0]["plan"]["orchestrator"] = (
        {"ccn_before": 44, "ccn_after": 9} if orchestrator is None else orchestrator
    )
    return plans


def test_a_staged_lead_plan_names_its_helpers_and_stage_one() -> None:
    core = next(i for i in _build(plans=_staged_plans()).items if i.kind == "refactor")
    assert core.title == "Split run into 3 helpers (+1 more step)"
    assert core.action.steps[0].text == (
        "Stage 1 of 3: Extract lines 20-35 of run into sum_rows(rows, limit) -> total "
        "(CCN 44 -> 9 after all 3)"
    )
    # Only the staged plan's step says so; the plan's next step reads as before.
    assert core.action.steps[1].text == (
        "Extract lines 40-41 of run into <name>(); name it for what the lines do"
    )


def test_a_staged_plan_without_its_complexity_figures_leaves_them_out() -> None:
    core = next(
        i for i in _build(plans=_staged_plans(2, orchestrator={})).items if i.kind == "refactor"
    )
    assert core.action.steps[0].text == (
        "Stage 1 of 2: Extract lines 20-35 of run into sum_rows(rows, limit) -> total"
    )


def test_a_finding_borrowing_a_staged_span_says_it_is_staged() -> None:
    plans = _staged_plans()
    plans[0].update(file_path="src/core.py", target_symbol="src/core.py::run")
    lone = _build(plans=plans, refactoring=[], performance=[]).lead
    assert lone.action.steps[0].text == (
        "Stage 1 of 3: Extract lines 20-35 of run into sum_rows(rows, limit) -> total "
        "(CCN 44 -> 9 after all 3)"
    )


@pytest.mark.parametrize(
    ("stages", "orchestrator"),
    [
        ("not a list", {"ccn_before": 44, "ccn_after": 9}),
        ([None, 3, "x"], {"ccn_before": 44, "ccn_after": 9}),
        ([{"span": {"start": 20, "end": 35}}], {"ccn_before": 44, "ccn_after": 9}),
        ([{"span": {"start": 20}}, {"span": None}], {"ccn_before": 44}),
        ([{"span": {"start": 20, "end": 35}}, {"span": {"start": 36, "end": 40}}], "bad"),
        ([{"span": {"start": 20, "end": 35}}, {"span": {"start": 36, "end": 40}}], {"ccn_after": 9}),
        ([{"span": {"start": "20", "end": "35"}}, {"span": {"start": "a", "end": "b"}}], {}),
        ([{"span": {"start": True, "end": True}}, {"span": {"start": 36, "end": 40}}], {}),
        ([{"span": {"start": 1.5, "end": 9.0}}, {"span": {"start": 36, "end": 40}}], {}),
    ],
)
def test_malformed_stages_never_crash_or_say_one_helper(stages, orchestrator) -> None:
    plans = copy.deepcopy(PLANS)
    plans[0]["plan"].update(stages=stages, orchestrator=orchestrator)
    core = next(i for i in _build(plans=plans).items if i.kind == "refactor")
    step = core.action.steps[0].text
    assert "into 1 helpers" not in step and "into 1 helpers" not in core.title
    if step.startswith("Stage 1 of 2"):
        assert step.endswith("sum_rows(rows, limit) -> total")
    else:
        assert step == "Extract lines 20-35 of run into sum_rows(rows, limit) -> total"


def test_an_unstaged_plan_reads_as_before() -> None:
    core = next(i for i in _build().items if i.kind == "refactor")
    assert core.title == (
        "Start breaking up run (CCN 44, 50 lines): first lift lines 20-35"
    )
    assert core.action.steps[0].text == "Extract lines 20-35 of run into sum_rows(rows, limit) -> total"


def _anchored_plans(anchors: dict, staged: bool = False) -> list:
    plans = _staged_plans() if staged else copy.deepcopy(PLANS)
    plans[0]["plan"]["anchors"] = anchors
    return plans


def test_an_anchored_step_quotes_the_text_its_span_starts_and_ends_with() -> None:
    anchors = {"first": "    total = 0", "last": "    return total", "unique_in": "file"}
    core = next(i for i in _build(plans=_anchored_plans(anchors)).items if i.kind == "refactor")
    step = core.action.steps[0]
    assert step.text == (
        "Extract run from `    total = 0` to `    return total` into sum_rows(rows, limit) -> total"
    )
    # The line stays as a hint; the anchors replace the line range in the text.
    assert step.line == 20 and "lines 20-35" not in step.text


def test_a_staged_anchored_step_quotes_its_first_stage_with_the_helper_brief() -> None:
    plans = _anchored_plans({"first": "a = 1", "last": "b = `x`", "unique_in": "file"}, staged=True)
    plans[0]["plan"]["new_symbol"] = {"signature_text": "def sum_rows(rows, limit):"}
    core = next(i for i in _build(plans=plans).items if i.kind == "refactor")
    # A backtick inside an anchor widens its fence; the text keeps the helper's brief
    # because a surface that shows only the text has no header beside it.
    assert core.action.steps[0].text == (
        "Stage 1 of 3: Extract run from `a = 1` to `` b = `x` `` into "
        "sum_rows(rows, limit) -> total (CCN 44 -> 9 after all 3)"
    )
    assert core.action.steps[0].signature == "def sum_rows(rows, limit):"


def test_an_anchor_unique_only_in_its_function_says_so() -> None:
    anchors = {"first": "x = 1\n    y = 2", "last": "return y", "unique_in": "function"}
    plans = _anchored_plans(anchors)
    plans[0].update(file_path="src/core.py", target_symbol="src/core.py::run")
    lone = _build(plans=plans, refactoring=[], performance=[]).lead
    # A finding with no plan of its own borrows the span with its anchors.
    assert lone.action.steps[0].text == (
        "Extract run from `x = 1\n    y = 2` to `return y` into sum_rows(rows, limit) -> total. "
        f"{ELSEWHERE_NOTE}"
    )


def test_a_staged_anchor_unique_only_in_its_function_says_so_after_the_stage() -> None:
    plans = _anchored_plans({"first": "a = 1", "last": "b = 2", "unique_in": "function"}, staged=True)
    core = next(i for i in _build(plans=plans).items if i.kind == "refactor")
    assert core.action.steps[0].text == (
        "Stage 1 of 3: Extract run from `a = 1` to `b = 2` into sum_rows(rows, limit) -> total "
        f"(CCN 44 -> 9 after all 3). {ELSEWHERE_NOTE}"
    )


def test_malformed_anchors_fall_back_to_lines() -> None:
    plans = _anchored_plans({"first": "a = 1", "last": None})
    core = next(i for i in _build(plans=plans).items if i.kind == "refactor")
    assert core.action.steps[0].text == "Extract lines 20-35 of run into sum_rows(rows, limit) -> total"


def test_a_plan_that_leaves_its_function_above_the_bar_can_wait() -> None:
    from repowise.core.analysis.health.worth import LOW_PRIORITY_LABEL

    rows = copy.deepcopy(REFACTORING)
    rows[0]["details"]["leaves_above_bar"] = (
        "leaves run at CCN 41, still above the break-up bar; more stages needed"
    )
    core = next(i for i in _build(refactoring=rows).items if i.kind == "refactor")
    assert core.tier == "later"
    assert ("tier", LOW_PRIORITY_LABEL["above_bar_after"]) in [
        (f.factor, f.value) for f in core.why_ranked
    ]
    # Without the reason the same plan is worth doing now.
    assert next(i for i in _build().items if i.kind == "refactor").tier == "now"


def test_a_finding_borrowing_a_plan_that_leaves_it_above_the_bar_says_so() -> None:
    plans = copy.deepcopy(PLANS)
    plans[0].update(
        file_path="src/core.py",
        target_symbol="src/core.py::run",
        rank_json='{"leaves_above_bar": "leaves run at CCN 41, still above the break-up bar; '
        'more stages needed"}',
    )
    lone = _build(plans=plans, refactoring=[], performance=[]).lead
    # The finding keeps its tier; only its step says the plan is not the whole fix.
    assert lone.tier != "later"
    assert lone.action.steps[0].text == (
        "Extract lines 20-35 of run into sum_rows(rows, limit) -> total. This plan alone "
        "leaves run at CCN 41, still above the break-up bar; more stages needed."
    )


def test_plans_without_above_bar_facts_read_as_before() -> None:
    # No rank facts, or rank facts without the reason: same steps, same tiers.
    before = _build()
    plans = copy.deepcopy(PLANS)
    for plan in plans:
        plan["rank_json"] = '{"position": 1}'
    for queue in (_build(plans=plans), _build(plans=copy.deepcopy(PLANS))):
        assert [(i.id, i.tier, [s.text for s in i.action.steps]) for i in queue.items] == [
            (i.id, i.tier, [s.text for s in i.action.steps]) for i in before.items
        ]
    assert all("This plan alone" not in s.text for i in before.items for s in i.action.steps)


def _hidden_plan(plans: list | None = None) -> tuple[list, list]:
    """The function's Extract Method plan is stored and open, but its
    opportunity recovers too little health to be an item of its own."""
    plans = copy.deepcopy(PLANS) if plans is None else plans
    plans[0].update(file_path="src/core.py", target_symbol="src/core.py::run")
    small = {**REFACTORING[1], "opportunity_id": "refop2_core_small", "file_path": "src/core.py"}
    return plans, [small]


def _run(plans: list, refactoring: list):
    queue = _build(plans=plans, refactoring=refactoring, performance=[])
    return next(i for i in queue.items if i.target.symbol == "run")


def test_a_finding_whose_plan_is_below_the_worth_floor_leads_with_that_plan() -> None:
    plans, refactoring = _hidden_plan()
    queue = _build(plans=plans, refactoring=refactoring, performance=[])
    run = next(i for i in queue.items if i.target.symbol == "run")
    assert queue.totals.excluded["below_min_worth"] == 1 and run.kind == "finding"
    assert run.title == (
        "Start breaking up run (CCN 44, 50 lines): first lift lines 20-35"
    )
    assert run.action.steps[0].text == (
        "Extract lines 20-35 of run into sum_rows(rows, limit) -> total"
    )
    assert run.source.plan_ids == ("refac2_big",)
    assert run.source.finding_ids == ("finding_c1",)
    assert run.next_call.tool == "get_health"
    assert run.next_call.arguments == {"plan_id": "refac2_big"}
    assert run.next_call.cli == "repowise health --plan refac2_big"
    assert "stored plan" not in run.confidence.reason + run.effort.basis
    # Ranked as the finding was: only what it says about its plan changes.
    before = _run(PLANS, refactoring)
    assert (run.id, run.rank, run.tier, run.value) == (
        before.id,
        before.rank,
        before.tier,
        before.value,
    )


def test_a_staged_plan_leading_a_finding_names_its_helpers() -> None:
    run = _run(*_hidden_plan(_staged_plans()))
    assert run.title == "Split run into 3 helpers"
    assert run.action.steps[0].text.startswith("Stage 1 of 3: ")


def test_a_led_title_says_when_the_plan_leaves_the_function_above_the_bar() -> None:
    plans, refactoring = _hidden_plan(_staged_plans())
    plans[0]["rank_json"] = (
        '{"leaves_above_bar": "leaves run at CCN 40, still above the break-up bar; '
        'more stages needed"}'
    )
    run = _run(plans, refactoring)
    assert run.title == "Split run into 3 helpers (more stages needed)"
    assert run.source.plan_ids == ("refac2_big",)


def test_a_plan_that_does_not_lower_the_function_leaves_the_finding_as_it_was() -> None:
    plans, refactoring = _hidden_plan()
    # Lifting all of run's complexity into one helper moves the problem, not shrinks it.
    plans[0]["evidence"] = {"slice_nloc": 45, "ccn_removed": 44}
    run = _run(plans, refactoring)
    assert run.title == "Break up run (CCN 44, 50 lines)"
    assert run.source.plan_ids == ()
    assert run.next_call.arguments == {"targets": ["src/core.py"], "include": ["biomarkers"]}


def test_a_dismissed_plan_never_gives_a_finding_its_step() -> None:
    plans, refactoring = _hidden_plan()
    plans[0]["status"] = "resolved"
    run = _run(plans, refactoring)
    assert run.title == "Break up run (CCN 44, 50 lines)"
    assert run.source.plan_ids == () and "Extract" not in run.action.steps[0].text


def _hotspot(function: str, commits: int) -> dict:
    return {
        "file_path": "src/core.py", "biomarker_type": "function_hotspot", "severity": "medium",
        "function_name": function, "health_impact": 0.4, "public_id": f"finding_fh_{function}",
        "dimension": "defect", "details": {"modification_count": commits},
    }


def test_a_function_item_shows_its_own_history_never_another_functions() -> None:
    findings = [*FINDINGS, _hotspot("Client.__init__", 7), _hotspot("Engine.run", 9)]
    core = next(i for i in _build(findings=findings).items if i.kind == "refactor")
    values = [c.value for c in core.context]
    assert "Engine.run changed in 9 commits" in values
    assert not any("__init__" in v for v in values)
    # File-level history still rides along.
    assert ("recent changes", "changed 12 times in 90 days") in [
        (c.label, c.value) for c in core.context
    ]


def test_a_function_item_says_its_file_is_imported_not_that_it_is() -> None:
    core = next(i for i in _build().items if i.kind == "refactor")
    assert core.why.endswith("its file is imported by 9 files, changed 12 times in 90 days.")
    assert core.risk.text == "Touches 1 file; its file is imported by 9 files."
    assert ("files that import its file", "9") in [(f.label, f.value) for f in core.facts]


def test_history_matches_the_items_qualified_symbol() -> None:
    from repowise.core.analysis.health.fix_first.build import _about

    # Same method name in two classes: only the item's own class.
    assert _about("Engine.run", "src/core.py::Engine.run")
    assert not _about("Other.run", "src/core.py::Engine.run")
    # A class item keeps its members, not a class sharing its prefix.
    assert _about("Engine.run", "Engine")
    assert not _about("EngineTest.run", "Engine")
    # File-level rows and items keep everything; a bare name matches by name.
    assert _about(None, "Engine.run") and _about("Other.run", None)
    assert _about("run", "Engine.run") and not _about("walk", "Engine.run")


def test_a_plan_targets_its_symbol_span_like_a_finding_does() -> None:
    """The target is the function's own span for every kind; the first edit's
    line belongs to the first step."""
    by_kind = {i.kind: i for i in _build(limit=None).items}
    refactor, finding = by_kind["refactor"], by_kind["finding"]
    assert (refactor.target.line_start, refactor.target.line_end) == (10, 60)
    assert refactor.action.steps[0].line == 20
    assert (finding.target.line_start, finding.target.line_end) == (5, 40)
