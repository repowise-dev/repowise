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
        "details": {"ccn": 14, "nloc": 50, "max_nesting": 4, "deepest_block": {"start": 20, "end": 30},
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


def test_a_build_file_counts_as_tooling_by_stored_origin_or_by_path() -> None:
    stored = _queue([_finding("src/a.py")], [_metric("src/a.py", code_origin="build")])
    assert stored.items == () and stored.totals.excluded["tooling"] == 1
    # An index stored before build files were classed says production; the
    # path still decides.
    path = "ktor-server/build.gradle.kts"
    old = _queue([_finding(path)], [_metric(path, code_origin="production")])
    assert old.items == () and old.totals.excluded["tooling"] == 1
    assert len(_queue([_finding("src/build_info.py")]).items) == 1


def test_a_deprecated_function_is_no_item() -> None:
    queue = _queue([_finding(deprecated=True)])
    assert queue.items == () and queue.totals.excluded["deprecated"] == 1
    assert len(_queue([_finding()]).items) == 1


# --- F2: one long dispatch on one value -------------------------------------------


def _helper(path: str = "src/core.py", start: int = 25, end: int = 40) -> dict:
    """A verified duplicate: an Extract Helper plan stored at another file."""
    return {"public_id": f"refac3_h{start}", "refactoring_type": "extract_helper",
            "file_path": "src/other.py", "target_symbol": "other",
            "plan": {"occurrences": [
                {"file": "src/other.py", "line_start": 5, "line_end": 20},
                {"file": path, "line_start": start, "line_end": end}]}}


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
    queue = _queue([_finding(dispatch_share=0.8)], plans=[_helper()])
    assert [i.target.symbol for i in queue.items] == ["run"]
    # A duplicate elsewhere in the file does not.
    away = _queue([_finding(dispatch_share=0.8)], plans=[_helper(start=200, end=220)])
    assert away.items == () and away.totals.excluded["inherent_dispatch"] == 1


def test_an_unverified_clone_finding_rescues_nothing() -> None:
    """``dry_violation`` also pairs import blocks and data literals."""
    queue = _queue([_finding(dispatch_share=0.8), _dry()])
    assert queue.items == () and queue.totals.excluded["inherent_dispatch"] == 1


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


# --- F4: every item names a concrete edit ----------------------------------------


def _extraction(start: int = 22, end: int = 34, ccn_removed: int = 3) -> dict:
    return {"public_id": f"refac2_x{start}", "refactoring_type": "extract_method",
            "file_path": "src/core.py", "target_symbol": "run",
            "evidence": {"slice_nloc": end - start, "ccn_removed": ccn_removed},
            "plan": {"span": {"start": start, "end": end}, "params": ["rows"],
                     "suggested_name": "total_rows"}}


def test_a_size_finding_starts_at_the_best_stored_extraction() -> None:
    plans = [_extraction(22, 34, 3), _extraction(40, 44, 1)]
    item = _queue([_finding()], plans=plans).lead
    assert item.action.steps[0].text == "Extract lines 22-34 of run into total_rows(rows)"
    assert item.action.steps[0].line == 22


def test_without_an_extraction_it_starts_at_the_deepest_block() -> None:
    item = _queue([_finding()]).lead
    assert item.action.steps[0].text == (
        "Start with lines 20-30, where it nests 4 deep: return early or move it into a helper"
    )
    assert (item.action.steps[0].file_path, item.action.steps[0].line) == ("src/core.py", 20)


def test_a_size_finding_with_no_concrete_step_is_no_candidate() -> None:
    bare = _finding()
    bare["details"] = {k: v for k, v in bare["details"].items() if k != "deepest_block"}
    queue = _queue([bare])
    assert queue.items == () and queue.totals.excluded["no_concrete_step"] == 1


def test_a_class_finding_without_a_plan_is_no_candidate() -> None:
    god = {**_finding(), "biomarker_type": "god_class", "function_name": "Store"}
    assert _queue([god]).totals.excluded["no_concrete_step"] == 1


def test_a_line_finding_names_its_line() -> None:
    handler = {**_finding(), "biomarker_type": "error_handling", "line_start": 77}
    step = _queue([handler]).lead.action.steps[0]
    assert (step.line, step.file_path) == (77, "src/core.py")


def _opportunity(kind: str, plan: dict | None) -> tuple[dict, list[dict]]:
    from tests.unit.health.fix_first_rows import REFACTORING

    row = {**REFACTORING[0], "lead_refactoring_type": kind, "lead_biomarker": None,
           "details": {**REFACTORING[0]["details"], "steps": [
               {**REFACTORING[0]["details"]["steps"][0], "refactoring_type": kind,
                "plan_id": "refac2_k", "target_symbol": "Store.save"}]}}
    plans = [{"public_id": "refac2_k", "refactoring_type": kind, "plan": plan}] if plan else []
    return row, plans


def _refactor_queue(kind: str, plan: dict | None):
    row, plans = _opportunity(kind, plan)
    return build_fix_first(metrics=[_metric()], refactoring=[row], plans=plans)


def _step_text(kind: str, plan: dict) -> str:
    """A step's text as an opportunity lists it, led by any step."""
    from repowise.core.analysis.health.fix_first.build import _refactor_step

    row, _plans = _opportunity(kind, plan)
    return _refactor_step(1, row["details"]["steps"][0], {"plan": plan}).text


def test_a_move_step_names_its_destination() -> None:
    assert _step_text("move_method", {"to_class": "Ledger"}) == "Move Store.save to Ledger"


def test_break_cycle_needs_the_import_line() -> None:
    edges = [{"from": "src/a.py", "to": "src/b.py"}]
    assert _refactor_queue("break_cycle", {"cut_edges": edges}).totals.excluded[
        "no_concrete_step"] == 1
    lined = _refactor_queue("break_cycle", {"cut_edges": [{**edges[0], "line": 7}]})
    assert lined.lead.action.steps[0].text == "Cut the import of b.py in a.py (line 7)"


def test_an_idiomatic_cycle_is_never_a_cut_to_make() -> None:
    edges = [{"from": "src/a.py", "to": "src/b.py", "line": 7}]
    plan = {"cut_edges": edges, "idiom": "same_directory"}
    assert _refactor_queue("break_cycle", plan).totals.excluded["no_concrete_step"] == 1
    text = _step_text("break_cycle", plan)
    assert text.startswith("Optional:") and "idiomatic" in text
    assert "Cut the import" not in text


def test_split_file_needs_named_groups() -> None:
    unnamed = {"groups": [{"symbols": ["a", "b"]}]}
    assert _refactor_queue("split_file", unnamed).totals.excluded["no_concrete_step"] == 1
    named = {"groups": [{"name": "io", "symbols": ["read"]}, {"name": "ui", "symbols": ["draw"]}]}
    assert _refactor_queue("split_file", named).lead.action.steps[0].text == (
        "Split core.py into io, ui"
    )


def test_an_extract_method_step_without_a_span_is_no_candidate() -> None:
    assert _refactor_queue("extract_method", {"params": []}).totals.excluded[
        "no_concrete_step"] == 1


# --- F7: string building in a bounded loop -------------------------------------


def _concat(**facets) -> dict:
    from tests.unit.health.fix_first_rows import _perf

    row = _perf("perf3_concat", "", biomarker_type="string_concat_in_loop", boundary_kind=None,
                fix_strategy="buffer_string_accumulation")
    row["details"] = {**row["details"], "facets": {**row["details"]["facets"], **facets}}
    return row


def _perf_queue(*rows):
    return build_fix_first(metrics=[_metric("src/repo.py")], performance=list(rows))


def test_string_concat_needs_a_loop_that_grows() -> None:
    assert len(_perf_queue(_concat()).items) == 1
    for magnitude in ("bounded", "unknown"):
        queue = _perf_queue(_concat(loop_magnitude=magnitude))
        assert queue.items == () and queue.totals.excluded["below_min_worth"] == 1


def test_other_causes_keep_an_unknown_loop() -> None:
    from tests.unit.health.fix_first_rows import _perf

    row = _perf("perf3_db", "src/db.py::fetch")
    row["details"] = {**row["details"], "facets": {"loop_magnitude": "unknown"}}
    assert len(_perf_queue(row).items) == 1


# --- F8: unknown loops no entry point reaches -----------------------------------


def _value(**facets) -> str:
    from tests.unit.health.fix_first_rows import _perf

    row = _perf("perf3_v", "src/db.py::fetch")
    row["details"] = {**row["details"], "facets": facets}
    item = _perf_queue(row).lead
    return next(f.value for f in item.why_ranked if f.factor in ("value", "value within later"))


def test_only_a_loop_known_to_grow_leads() -> None:
    from repowise.core.analysis.health.worth import LOW_PRIORITY_LABEL
    from tests.unit.health.fix_first_rows import _perf

    def lead(**facets):
        row = _perf("perf3_t", "src/db.py::fetch")
        row["details"] = {**row["details"], "facets": facets}
        return _perf_queue(row).lead

    unknown = lead(loop_magnitude="unknown", exposure="entry_reachable")
    assert unknown.tier == "later"
    assert ("tier", LOW_PRIORITY_LABEL["unmeasured_cost"]) in [
        (f.factor, f.value) for f in unknown.why_ranked
    ]
    assert lead(loop_magnitude="grows_with_data").tier != "later"


def test_an_unknown_loop_no_entry_reaches_drops_a_step() -> None:
    assert _value(loop_magnitude="unknown", exposure="not_entry_reachable") == "0"
    assert _value(loop_magnitude="unknown") == "0"


def test_reach_keeps_an_unknown_loop_a_step_below_a_known_one() -> None:
    # An unproven cost never earns the value a loop known to grow does.
    assert _value(loop_magnitude="unknown", exposure="entry_reachable") == "1"
    assert _value(loop_magnitude="grows_with_data", exposure="not_entry_reachable") == "2"


# --- lift: a duplicate inside the function ---------------------------------------


def _ranked(item, factor: str) -> str:
    return next(f.value for f in item.why_ranked if f.factor == factor)


def test_a_duplicate_inside_lifts_a_complexity_unit_one_step() -> None:
    plain = _queue([_finding()]).lead
    lifted = _queue([_finding()], plans=[_helper()]).lead
    # CCN 14 is near the bar, so both are later; the lift still orders them.
    value = "value within later"
    assert int(_ranked(lifted, value)) == int(_ranked(plain, value)) + 1
    assert _ranked(lifted, "duplicate inside") == "yes"
    assert "duplicated" in lifted.why and "duplicated" not in plain.why


def test_a_duplicate_elsewhere_in_the_file_does_not_lift() -> None:
    item = _queue([_finding()], plans=[_helper(start=200, end=220)]).lead
    assert _ranked(item, "duplicate inside") == "no"


def test_an_unverified_clone_finding_does_not_lift() -> None:
    item = _queue([_finding(), _dry()]).lead
    assert _ranked(item, "duplicate inside") == "no"


def test_a_duplicate_does_not_lift_a_line_finding() -> None:
    handler = {**_finding(), "biomarker_type": "error_handling", "line_start": 30}
    item = _queue([handler], plans=[_helper()]).lead
    assert _ranked(item, "duplicate inside") == "no"


# --- may_lead: a cause that has not cleared the bar never leads ----------------


def _lazy(may_lead: bool) -> dict:
    from tests.unit.health.fix_first_rows import _perf

    row = _perf("perf3_lazy", "src/db.py::fetch", biomarker_type="lazy_load_in_loop")
    row["details"] = {**row["details"], "may_lead": may_lead}
    return row


def test_a_cause_that_may_not_lead_is_listed_second() -> None:
    small = _finding("src/core.py", ccn=16, nloc=40)
    queue = build_fix_first(
        metrics=[_metric(), _metric("src/repo.py")], findings=[small],
        performance=[_lazy(False)],
    )
    assert [i.kind for i in queue.items] == ["finding", "perf_fix"]
    assert next(f.value for f in queue.items[1].why_ranked if f.factor == "value") == "4"


def test_a_cause_that_may_lead_still_leads() -> None:
    small = _finding("src/core.py", ccn=16, nloc=40)
    queue = build_fix_first(
        metrics=[_metric(), _metric("src/repo.py")], findings=[small],
        performance=[_lazy(True)],
    )
    assert [i.kind for i in queue.items] == ["perf_fix", "finding"]


def test_a_lone_cause_that_may_not_lead_is_still_shown() -> None:
    queue = build_fix_first(metrics=[_metric("src/repo.py")], performance=[_lazy(False)])
    assert [i.kind for i in queue.items] == ["perf_fix"]


# --- wording: why, context, labels ----------------------------------------------


def test_why_says_why_it_matters_not_the_titles_size() -> None:
    huge = _finding(ccn=249, nloc=1280, max_nesting=7)
    item = _queue([huge]).lead
    assert item.title == "Break up run (CCN 249, 1,280 lines)"
    assert "249" not in item.why and "1,280" not in item.why and "CCN" not in item.why
    assert item.why == (
        "run has many independent paths through it; 9 files import it, "
        "changed 12 times in 90 days."
    )


def test_why_names_coverage_when_a_report_measured_it() -> None:
    covered = _queue([_finding()], [_metric(line_coverage_pct=41.6)]).lead
    assert covered.why.endswith("tests run 42% of its lines.")
    bare = _queue([_finding()], [_metric(line_coverage_pct=0.0)]).lead
    assert bare.why.endswith("no test runs it.")
    assert "test" not in _queue([_finding()]).lead.why


def test_context_is_short_plain_sentences() -> None:
    scatter = {"file_path": "src/core.py", "biomarker_type": "co_change_scatter",
               "severity": "high", "health_impact": 2.0, "public_id": "finding_s",
               "dimension": "defect", "reason": "co-changes with 35 distinct files (top 3.7%)",
               "details": {"scatter": 35, "co_change_scatter_pct": 96.3}}
    hotspot = {**scatter, "biomarker_type": "function_hotspot", "function_name": "run",
               "health_impact": 1.0, "public_id": "finding_fh",
               "reason": "run has been modified across 3 commits (repo p80=2)",
               "details": {"modification_count": 3, "repo_p80": 2, "ccn": 14}}
    item = _queue([_finding(), scatter, hotspot], [_metric(contributor_count=3)]).lead
    assert [(c.label, c.value) for c in item.context] == [
        ("recent changes", "changed 12 times in 90 days; 3 people have worked on it"),
        ("ripple", "it changes together with 35 other files"),
        ("keeps changing", "run changed in 3 commits"),
    ]
    for c in item.context:
        assert "%" not in c.value and "p80" not in c.value and "top " not in c.value


def test_no_text_field_carries_a_marker_id() -> None:
    from repowise.core.analysis.health.scoring import _BIOMARKER_CATEGORY

    queue = _queue([_finding(), {**_finding("src/b.py"), "biomarker_type": "error_handling",
                                 "line_start": 9}])
    for item in queue.items:
        texts = [item.title, item.why, item.gain.text, item.action.summary,
                 *(s.text for s in item.action.steps), *(f.value for f in item.facts),
                 *(c.value for c in item.context), *(r.value for r in item.why_ranked)]
        for value in texts:
            assert not any(m in value for m in _BIOMARKER_CATEGORY if "_" in m), value
    assert ("finding", "Complex method") in [(f.label, f.value) for f in queue.items[0].facts]


def test_a_module_scope_cause_reads_module_scope_of_its_file() -> None:
    from tests.unit.health.fix_first_rows import _perf

    row = _perf("perf3_mod", "src/db.py::fetch", intervention_symbol="src/repo.py::__module__")
    row["details"]["plan"]["steps"][0]["symbol"] = "src/repo.py::__module__"
    item = _perf_queue(row).lead
    assert item.title == "Batch the database calls loops make in module scope of repo.py"
    assert "__module__" not in item.why and "__module__" not in item.action.steps[0].text
    assert item.target.symbol is None


# --- the strongest performance fixes share the top value band ---------------------


def test_a_production_reachable_growing_db_loop_is_in_the_top_band() -> None:
    from repowise.core.analysis.health.queue.value import VALUE_MAX

    assert _value(loop_magnitude="grows_with_data", exposure="entry_reachable") == str(VALUE_MAX)


def test_it_outranks_a_large_function_that_needs_judgment_on_score() -> None:
    from tests.unit.health.fix_first_rows import _perf

    huge = _finding(ccn=249, nloc=1280, max_nesting=7)
    queue = build_fix_first(
        metrics=[_metric(), _metric("src/repo.py")], findings=[huge],
        performance=[_perf("perf3_top", "src/db.py::fetch")],
    )
    # Both sit in the top band; the perf fix is listed beside the large function.
    assert {i.kind for i in queue.items[:2]} == {"finding", "perf_fix"}


def test_without_reach_it_stays_below_the_top_band() -> None:
    assert _value(loop_magnitude="grows_with_data", exposure="not_entry_reachable") == "2"


# --- titles and steps name what they mean -----------------------------------------


def test_an_extract_class_step_names_the_members_it_moves() -> None:
    plan = {"groups": [{"methods": ["load", "save", "flush"], "fields": ["db"]},
                       {"methods": ["render"], "fields": ["tpl"]}]}
    assert _step_text("extract_class", plan) == "Move render out of Store.save into a new class"


def test_only_a_size_finding_is_titled_break_up() -> None:
    wide = {**_finding(ccn=12, nloc=40, max_nesting=8), "biomarker_type": "complex_conditional"}
    item = _queue([wide]).lead
    assert item.title == "Simplify the condition in run"


def test_a_one_line_block_reads_as_one_line() -> None:
    item = _queue([_finding(deepest_block={"start": 42, "end": 42})]).lead
    assert item.action.steps[0].text.startswith("Start with line 42, where it nests 4 deep")


# --- kinds the baseline raters found not worth doing -------------------------------


def test_extract_class_and_move_method_plans_wait_for_their_audit() -> None:
    plan = {"groups": [{"methods": ["load", "save"], "fields": ["db"]}]}
    split = _refactor_queue("extract_class", plan)
    assert split.items == () and split.totals.excluded["kind_unaudited"] == 1
    moved = _refactor_queue("move_method", {"to_class": "Ledger"})
    assert moved.items == () and moved.totals.excluded["kind_unaudited"] == 1
    assert moved.totals.excluded["low_value_kind"] == 0


def test_large_method_and_low_cohesion_findings_are_low_value() -> None:
    for marker in ("large_method", "low_cohesion"):
        queue = _queue([{**_finding(), "biomarker_type": marker}])
        assert queue.items == () and queue.totals.excluded["low_value_kind"] == 1


def test_a_split_file_plan_and_a_complex_method_stay() -> None:
    named = {"groups": [{"name": "io", "symbols": ["read"]}]}
    assert len(_refactor_queue("split_file", named).items) == 1
    assert len(_queue([_finding()]).items) == 1


def test_a_rust_panic_path_is_no_fix_first_candidate() -> None:
    """An unwrap or panic is a crash path, not a failure the handler hides."""
    rows = [
        {**_finding(f"src/{kind}.rs"), "biomarker_type": "error_handling", "line_start": 9,
         "details": {"kind": kind}}
        for kind in ("unsafe_unwrap", "panic_macro")
    ]
    queue = _queue(rows)
    assert queue.items == () and queue.totals.excluded["low_value_kind"] == 2
    swallowed = {**rows[0], "file_path": "src/a.py", "details": {"kind": "swallowed_catch"}}
    assert [i.target.file_path for i in _queue([swallowed]).items] == ["src/a.py"]


def test_a_perf_step_names_its_function_unless_the_action_already_does() -> None:
    from repowise.core.analysis.health.fix_first.build import _perf_step_text

    quoted = {"action": "Batch the per-row session.get calls", "symbol": "src/db.py::Store.get"}
    assert _perf_step_text(quoted, "src/db.py") == f"{quoted['action']} (Store.get)"
    named = {"action": "Pass every key to get at once", "symbol": "src/db.py::Store.get"}
    assert _perf_step_text(named, "src/db.py") == named["action"]


def test_a_span_that_awaits_is_extracted_into_an_awaited_async_helper() -> None:
    from repowise.core.analysis.health.fix_first import text

    assert text.signature("load_rows", ["db"], ["rows"]) == "load_rows(db) -> rows"
    assert text.signature("load_rows", ["db"], ["rows"], is_async=True) == (
        "async load_rows(db) -> rows, awaited at the call site"
    )
    assert text.signature(None, ["db"], [], is_async=True) == (
        "an async helper taking (db), awaited at the call site"
    )
