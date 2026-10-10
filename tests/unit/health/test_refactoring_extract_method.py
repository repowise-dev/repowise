"""Unit tests for the Extract Method slicer + detector.

Best-effort: skip when the Python tree-sitter pack is missing.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import analyze_file, find_extractions
from repowise.core.analysis.health.refactoring import detect_refactorings
from repowise.core.analysis.health.refactoring.extract_method import (
    ExtractMethodDetector,
    _worth_extracting,
    jsx_plumbing_dominates,
    recovered_share,
)
from repowise.core.analysis.health.refactoring.models import RefactoringContext

# A long function whose tail (compute-average loop) is a clean extraction.
_PROCESS = """
def process(records, threshold):
    results = []
    errors = 0
    for r in records:
        if r is None:
            errors += 1
            continue
        results.append(r)
    total = 0
    count = 0
    for v in results:
        if v > threshold:
            total += v
            count += 1
        else:
            total -= v
    average = total / count if count else 0
    return average, errors
"""


@dataclass
class _Finding:
    biomarker_type: str
    function_name: str
    line_start: int
    health_impact: float


def _require_python() -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for python")
    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")


def _analyses(src: str):
    _require_python()
    res = analyze_file("m.py", "python", textwrap.dedent(src).encode(), flagged_only=False)
    if res.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for python")
    return res.functions


def _first(src: str):
    fns = _analyses(src)
    assert fns
    return fns[0]


# -- slicer ---------------------------------------------------------------------


def test_finds_clean_extraction_with_inferred_signature():
    lmap = get_language_map("python")
    extractions = find_extractions(_first(_PROCESS), lmap)
    assert extractions, "expected at least one extraction"
    best = extractions[0]
    # The strongest extraction is the compute-average tail.
    assert "average" in best.returns
    assert "results" in best.params and "threshold" in best.params
    # Single clean return, bounded params.
    assert len(best.returns) <= 1
    assert best.ccn_removed >= 1
    assert best.slice_nloc >= 5


def test_no_extraction_when_every_span_has_a_jump():
    # A guard-clause cascade: every span contains a return, so nothing is a
    # single-exit slice -> no candidate.
    lmap = get_language_map("python")
    src = """
        def classify(x):
            if x < 0:
                return "neg"
            if x == 0:
                return "zero"
            if x < 10:
                return "small"
            if x < 100:
                return "medium"
            return "large"
        """
    extractions = find_extractions(_first(src), lmap)
    assert extractions == []


def test_whole_function_body_is_not_extracted():
    lmap = get_language_map("python")
    src = """
        def f(a, b):
            x = a + b
            y = x * 2
            z = y - a
            return z
        """
    # The only span covering the whole body is excluded; the remaining spans
    # carry no decision point, so nothing qualifies.
    assert find_extractions(_first(src), lmap) == []


def test_extractions_are_deterministic():
    lmap = get_language_map("python")
    fn = _first(_PROCESS)

    def serialize():
        return [
            (e.start_line, e.end_line, e.params, e.returns, e.ccn_removed)
            for e in find_extractions(fn, lmap)
        ]

    first = serialize()
    for _ in range(3):
        assert serialize() == first


# -- prefix-sum equivalence -------------------------------------------------------
#
# find_extractions computes span metrics via per-statement prefix sums. This
# reference reimplements the original per-span _span_metrics enumeration; the
# two must produce identical candidate lists on any input.


def _find_extractions_reference(analysis, lmap):
    from repowise.core.analysis.health.dataflow.slice import (
        _MAX_BODY_SHARE,
        _MAX_CANDIDATES,
        _MAX_PARAMS,
        _MAX_RETURNS,
        _MIN_CCN_REMOVED,
        _MIN_SLICE_NLOC,
        _MIN_STMTS,
        Extraction,
        _declared_before_read,
        _exit_macros,
        _function_lines,
        _infer_in_out,
        _loop_carry_free,
        _outs_definitely_assigned,
        _reads_observing,
        _Scan,
        _sorted,
        _span_metrics,
        _stmts_nloc,
        _unwrap_container,
        _var_lines,
        all_blocks,
    )

    fn_node = analysis.fn_node
    if fn_node is None:
        return []
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    body_container = _unwrap_container(body, lmap.block_kinds)
    lines = _function_lines(fn_node)
    body_nloc = _stmts_nloc(body_container.named_children, lines)
    def_lines, use_lines = _var_lines(analysis.def_use)
    declared_first = _declared_before_read(analysis.def_use)
    reads = _reads_observing(analysis, def_lines)
    decision_kinds = (
        lmap.branch_kinds
        | lmap.loop_kinds
        | lmap.case_kinds
        | lmap.catch_kinds
        | lmap.boolean_operator_kinds
    )
    jump_kinds = (
        lmap.return_kinds
        | lmap.raise_kinds
        | lmap.break_kinds
        | lmap.continue_kinds
        | lmap.yield_kinds
    )
    scope_kinds = lmap.function_kinds | lmap.lambda_kinds
    tail_stmt_kinds = (
        lmap.statement_wrapper_kinds | lmap.local_decl_kinds
        if lmap.statement_wrapper_kinds
        else None
    )

    out = []
    evaluated = 0
    for block, loop in all_blocks(fn_node, lmap.block_kinds, scope_kinds, lmap.loop_kinds):
        stmts = block.named_children
        n = len(stmts)
        is_body = block.id == body_container.id
        for i in range(n):
            for j in range(i, n):
                evaluated += 1
                if evaluated > _MAX_CANDIDATES:
                    return _sorted(out)
                length = j - i + 1
                if length < _MIN_STMTS:
                    continue
                if is_body and length == n:
                    continue
                if (
                    tail_stmt_kinds is not None
                    and j == n - 1
                    and stmts[j].type not in tail_stmt_kinds
                ):
                    continue
                span = stmts[i : j + 1]
                decisions, has_jump, has_await, *_ = _span_metrics(
                    span,
                    _Scan(
                        decision_kinds,
                        jump_kinds,
                        scope_kinds,
                        _exit_macros(lmap),
                        (lmap.await_kinds, lmap.await_scope_kinds),
                    ),
                )
                if has_jump or decisions < _MIN_CCN_REMOVED:
                    continue
                slice_nloc = _stmts_nloc(span, lines)
                if slice_nloc < _MIN_SLICE_NLOC or slice_nloc >= _MAX_BODY_SHARE * body_nloc:
                    continue
                s = span[0].start_point[0] + 1
                e = span[-1].end_point[0] + 1
                params, returns = _infer_in_out(
                    def_lines, use_lines, s, e, declared_first, reads=reads
                )
                if len(params) > _MAX_PARAMS or len(returns) > _MAX_RETURNS:
                    continue
                if not _outs_definitely_assigned(span, returns, def_lines, lmap):
                    continue
                if loop is not None and not _loop_carry_free(
                    span, loop, s, e, def_lines, use_lines, lmap
                ):
                    continue
                out.append(
                    Extraction(
                        start_line=s,
                        end_line=e,
                        params=params,
                        returns=returns,
                        slice_nloc=slice_nloc,
                        ccn_removed=decisions,
                        needs_async=has_await,
                    )
                )
    return _sorted(out)


_NESTED = """
def transform(items, flags):
    out = []
    for it in items:
        if it in flags:
            for k in range(3):
                if k and it:
                    out.append((it, k))
        else:
            while it > 0:
                it -= 1
                out.append(it)
    def helper(v):
        if v:
            return -v
        return v
    total = 0
    for o in out:
        total += helper(o if isinstance(o, int) else o[1])
    if total > 10 or len(out) > 5:
        total = total // 2
    return total
"""

_JUMPY = """
def scan(rows):
    hits = 0
    for r in rows:
        if r is None:
            continue
        if r < 0:
            break
        hits += 1
    try:
        rate = hits / len(rows)
    except ZeroDivisionError:
        rate = 0.0
    if rate > 0.5:
        hits += 1
    return hits, rate
"""


@pytest.mark.parametrize("src", [_PROCESS, _NESTED, _JUMPY])
def test_prefix_sum_matches_per_span_reference(src):
    lmap = get_language_map("python")
    fn = _first(src)
    assert find_extractions(fn, lmap) == _find_extractions_reference(fn, lmap)


def test_prefix_sum_matches_per_span_reference_go():
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing")
    if _get_language("go") is None:
        pytest.skip("tree-sitter language pack missing for go")
    src = textwrap.dedent(
        """
        package main

        func Transform(items []int, limit int) int {
            total := 0
            count := 0
            for _, it := range items {
                if it > limit {
                    total += it
                    count++
                } else {
                    total -= it
                }
            }
            avg := 0
            if count > 0 {
                avg = total / count
            }
            for i := 0; i < avg; i++ {
                if i%2 == 0 {
                    total += i
                }
            }
            return total
        }
        """
    )
    res = analyze_file("m.go", "go", src.encode(), flagged_only=False)
    if res.stats.functions_seen == 0:
        pytest.skip("go parse unavailable")
    lmap = get_language_map("go")
    for fn in res.functions:
        assert find_extractions(fn, lmap) == _find_extractions_reference(fn, lmap)


# -- detector -------------------------------------------------------------------


def _ctx(src: str, findings):
    return RefactoringContext(
        file_path="m.py",
        language="python",
        nloc=100,
        findings=findings,
        function_analyses=_analyses(src),
    )


def _process_sources() -> dict[str, tuple[str, str]]:
    """``language -> (extension, source)`` for one shape in every language the
    slicer has a dialect for. The source is the same algorithm spelled the way
    each language writes it, so a name difference cannot come from the code."""
    py = textwrap.dedent(_PROCESS)
    java = textwrap.dedent(
        """
        class Demo {
            int process(int[] records, int threshold) {
                int errors = 0;
                java.util.List<Integer> results = new java.util.ArrayList<>();
                for (int r : records) {
                    if (r < 0) {
                        errors++;
                        continue;
                    }
                    results.add(r);
                }
                int total = 0;
                int count = 0;
                for (int v : results) {
                    if (v > threshold) {
                        total += v;
                        count++;
                    } else {
                        total -= v;
                    }
                }
                int average = 0;
                if (count > 0) {
                    average = total / count;
                }
                return average + errors;
            }
        }
        """
    )
    ts = textwrap.dedent(
        """
        function process(records: number[], threshold: number): number {
            const results: number[] = [];
            let errors = 0;
            for (const r of records) {
                if (r < 0) {
                    errors += 1;
                    continue;
                }
                results.push(r);
            }
            let total = 0;
            let count = 0;
            for (const v of results) {
                if (v > threshold) {
                    total += v;
                    count += 1;
                } else {
                    total -= v;
                }
            }
            let average = 0;
            if (count > 0) {
                average = total / count;
            }
            return average + errors;
        }
        """
    )
    go = textwrap.dedent(
        """
        package main

        func process(records []int, threshold int) int {
            errors := 0
            results := []int{}
            for _, r := range records {
                if r < 0 {
                    errors++
                    continue
                }
                results = append(results, r)
            }
            total := 0
            count := 0
            for _, v := range results {
                if v > threshold {
                    total += v
                    count++
                } else {
                    total -= v
                }
            }
            average := 0
            if count > 0 {
                average = total / count
            }
            return average + errors
        }
        """
    )
    rust = textwrap.dedent(
        """
        fn process(records: &[i32], threshold: i32) -> i32 {
            let mut errors = 0;
            let mut results: Vec<i32> = Vec::new();
            for r in records {
                if *r < 0 {
                    errors += 1;
                    continue;
                }
                results.push(*r);
            }
            let mut total = 0;
            let mut count = 0;
            for v in &results {
                if *v > threshold {
                    total += v;
                    count += 1;
                } else {
                    total -= v;
                }
            }
            let mut average = 0;
            if count > 0 {
                average = total / count;
            }
            average + errors
        }
        """
    )
    cpp = textwrap.dedent(
        """
        int process(int* records, int n, int threshold) {
            int errors = 0;
            std::vector<int> results;
            for (int i = 0; i < n; i++) {
                if (records[i] < 0) {
                    errors++;
                    continue;
                }
                results.push_back(records[i]);
            }
            int total = 0;
            int count = 0;
            for (int v : results) {
                if (v > threshold) {
                    total += v;
                    count++;
                } else {
                    total -= v;
                }
            }
            int average = 0;
            if (count > 0) {
                average = total / count;
            }
            return average + errors;
        }
        """
    )
    return {
        "python": ("py", py),
        "java": ("java", java),
        "typescript": ("ts", ts),
        "go": ("go", go),
        "rust": ("rs", rust),
        "cpp": ("cpp", cpp),
    }


def _detect_in(language: str, ext: str, src: str):
    from repowise.core.ingestion.parser import _get_language

    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    res = analyze_file(f"m.{ext}", language, src.encode(), flagged_only=False)
    if res.stats.functions_seen == 0:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    fn = res.functions[0]
    ctx = RefactoringContext(
        file_path=f"m.{ext}",
        language=language,
        nloc=100,
        findings=[_Finding("complex_method", fn.name, fn.start_line, 1.5)],
        function_analyses=res.functions,
    )
    plans = [s for s in ExtractMethodDetector().detect(ctx) if s.refactoring_type == "extract_method"]
    assert plans, f"no extract_method plan for {language}"
    return plans[0]


def test_detector_emits_suggestion_for_flagged_function():
    findings = [_Finding("complex_method", "process", line_start=2, health_impact=1.5)]
    suggestions = ExtractMethodDetector().detect(_ctx(_PROCESS, findings))
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.refactoring_type == "extract_method"
    assert s.target_symbol == "process"
    assert s.source_biomarker == "complex_method"
    # Credited the share of the function's decision points the span moves.
    fn = _first(_PROCESS)
    share = s.evidence["ccn_removed"] / fn.ccn
    assert 0 < share < 1
    assert s.impact_delta == round(1.5 * share, 3)
    # Plan shape is the locked schema.
    assert set(s.plan) == {
        "span",
        "params",
        "returns",
        "suggested_name",
        "needs_async",
        "new_symbol",
        "call_site",
    }
    assert s.plan["needs_async"] is False
    # A module-level function: the helper is a plain function too.
    assert s.plan["new_symbol"] == {
        "kind": "function",
        "async": False,
        "receiver": None,
        "uses_receiver": False,
        "assigns": [],
        "params": s.plan["new_symbol"]["params"],
        "returns": s.plan["new_symbol"]["returns"],
        "signature_text": s.plan["new_symbol"]["signature_text"],
        "return_text": "return average",
    }
    assert s.plan["call_site"]["replace_span"] == s.plan["span"]
    assert set(s.plan["span"]) == {"start", "end"}
    assert set(s.evidence) == {"slice_nloc", "ccn_removed"}
    # A categorical claim, not a count: extraction is local, so there is nothing
    # to measure. The old ``{"callers_count": 0}`` was a hardcoded literal that
    # no consumer could tell apart from a measured zero.
    assert s.blast_radius == {"scope": "local"}
    assert "callers_count" not in s.blast_radius
    assert s.confidence in ("medium", "high")


def test_plan_carries_a_computed_name_not_a_hardcoded_none():
    """``suggested_name`` was ``None`` on every extract_method plan ever stored
    (854 of 854 on this repo's index) while Extract Helper computed one, so the
    field's meaning depended on which detector wrote it and every surface fell
    through to a generic "helper". It is now always a real identifier."""
    findings = [_Finding("complex_method", "process", line_start=2, health_impact=1.5)]
    s = ExtractMethodDetector().detect(_ctx(_PROCESS, findings))[0]
    # The slice's single OUT value is ``average``, so the span is by
    # construction the code that computes it.
    assert s.plan["returns"] == ["average"]
    # In Python's private form.
    assert s.plan["suggested_name"] == "_compute_average"


class _Extraction:
    def __init__(self, returns):
        self.returns = list(returns)


class _Analysis:
    def __init__(self, name):
        self.name = name


def test_suggested_name_unit():
    name = ExtractMethodDetector._suggested_name
    # Exactly one OUT -> name the helper for what it produces, in the language's
    # own convention.
    assert name(_Analysis("process"), _Extraction(["average"]), "python") == "compute_average"
    # Non-identifier characters normalised through the shared slug.
    assert name(_Analysis("p"), _Extraction(["total-count"]), "python") == "compute_total_count"
    # A call with no language keeps the snake_case default (the pre-existing
    # behaviour), so an unset ``ctx.language`` degrades rather than changes shape.
    assert name(_Analysis("process"), _Extraction(["average"])) == "compute_average"


def test_suggested_name_none_cases_are_unchanged_by_the_language():
    """Control: the no-anchor rules this change must not touch. Each of these
    was ``None`` before it and must stay ``None`` after, in every language."""
    name = ExtractMethodDetector._suggested_name
    # No single OUT -> no anchor, so no name. The enclosing function's name
    # described the span's context, never the span, and collided with every
    # sibling plan in the file.
    assert name(_Analysis("run_pipeline"), _Extraction([])) is None
    assert name(_Analysis("run_pipeline"), _Extraction(["a", "b"])) is None
    # A return name that slugs to nothing must not yield "compute_".
    assert name(_Analysis("process"), _Extraction(["___"])) is None
    # An OUT named for its role, not the product: "compute_result" names
    # nothing the reader did not already know.
    assert name(_Analysis("process"), _Extraction(["result"])) is None
    # Nothing usable at all -> no name at all.
    assert name(_Analysis("___"), _Extraction([])) is None
    # And none of the above becomes a name just because the language is camelCase.
    for lang in ("java", "go", "typescript", "cpp", "python"):
        assert name(_Analysis("process"), _Extraction([]), lang) is None
        assert name(_Analysis("process"), _Extraction(["result"]), lang) is None
        assert name(_Analysis("process"), _Extraction(["___"]), lang) is None


def test_suggested_name_follows_the_language_convention():
    name = ExtractMethodDetector._suggested_name
    # camelCase languages.
    for lang in ("java", "go", "typescript", "tsx", "javascript", "jsx", "svelte", "vue"):
        assert name(_Analysis("process"), _Extraction(["average"]), lang) == "computeAverage", lang
    # snake_case languages, and C++ which has no single convention.
    for lang in ("python", "rust", "cpp"):
        assert name(_Analysis("process"), _Extraction(["average"]), lang) == "compute_average", lang
    # An unknown language falls back rather than raising.
    assert name(_Analysis("process"), _Extraction(["average"]), "cobol") == "compute_average"
    # A language whose file has several words keeps all of them.
    assert (
        name(_Analysis("process"), _Extraction(["total_count"]), "java") == "computeTotalCount"
    )


def test_suggested_name_keeps_the_out_values_own_casing():
    name = ExtractMethodDetector._suggested_name
    # ``meanValue`` must survive as word boundaries. Lowercasing the whole label
    # first (the old slug) gave ``compute_meanvalue``, neither camelCase nor
    # snake_case.
    assert name(_Analysis("process"), _Extraction(["meanValue"]), "java") == "computeMeanValue"
    assert name(_Analysis("process"), _Extraction(["mean_value"]), "java") == "computeMeanValue"
    assert name(_Analysis("process"), _Extraction(["MeanValue"]), "java") == "computeMeanValue"
    assert name(_Analysis("process"), _Extraction(["meanValue"]), "python") == "compute_mean_value"
    # A run of capitals stays one word: splitting it needs a dictionary.
    assert name(_Analysis("process"), _Extraction(["HTTPStatus"]), "java") == "computeHttpstatus"
    # The role-word guard still sees a multi-word out value's joined slug.
    assert name(_Analysis("process"), _Extraction(["item_value"]), "java") == "computeItemValue"


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("python", "_compute_average"),
        ("rust", "compute_average"),
        ("cpp", "compute_average"),
        ("java", "computeAverage"),
        ("typescript", "computeAverage"),
        ("go", "computeAverage"),
    ],
)
def test_detector_names_the_helper_in_the_files_language(language: str, expected: str):
    ext, src = _process_sources()[language]
    plan = _detect_in(language, ext, src)
    assert plan.plan["returns"] == ["average"], language
    assert plan.plan["suggested_name"] == expected, language


def test_detector_silent_without_matching_finding():
    # The function is analysed but no method biomarker fired -> no suggestion.
    suggestions = ExtractMethodDetector().detect(_ctx(_PROCESS, findings=[]))
    assert suggestions == []


def test_detector_silent_without_analyses():
    ctx = RefactoringContext(
        file_path="m.py",
        language="python",
        nloc=100,
        findings=[_Finding("complex_method", "process", 2, 1.0)],
        function_analyses=[],
    )
    assert ExtractMethodDetector().detect(ctx) == []


def test_detector_registered_in_registry():
    # The detector self-registers, so the generic runner picks it up.
    findings = [_Finding("large_method", "process", line_start=2, health_impact=2.0)]
    suggestions = detect_refactorings(_ctx(_PROCESS, findings))
    assert any(s.refactoring_type == "extract_method" for s in suggestions)


def test_detector_is_deterministic():
    findings = [_Finding("complex_method", "process", 2, 1.5)]

    def run():
        s = ExtractMethodDetector().detect(_ctx(_PROCESS, findings))[0]
        return (s.target_symbol, s.plan["span"], tuple(s.plan["params"]), tuple(s.plan["returns"]))

    first = run()
    for _ in range(3):
        assert run() == first


# -- proportional impact and minimum worth ---------------------------------------


@dataclass
class _Shape:
    ccn: int
    nloc: int


@dataclass
class _Span:
    ccn_removed: int
    slice_nloc: int


def test_share_follows_what_the_biomarker_measures():
    fn, span = _Shape(12, 40), _Span(6, 10)
    assert recovered_share("complex_method", fn, span) == 0.5
    assert recovered_share("large_method", fn, span) == 0.25
    assert recovered_share("brain_method", fn, span) == 0.5
    # Capped: a span cannot recover more than the whole finding.
    assert recovered_share("large_method", _Shape(2, 10), _Span(2, 20)) == 1.0


def test_no_biomarker_credits_more_than_the_decision_point_share():
    # A long flat span (137 code lines, 2 decision points out of 40) moves
    # straight-line code: the size share must not stand in for the split.
    fn, span = _Shape(40, 200), _Span(2, 137)
    assert recovered_share("large_method", fn, span) == pytest.approx(2 / 40)
    assert recovered_share("brain_method", fn, span) == pytest.approx(2 / 40)


def test_two_decision_points_out_of_a_tiny_size_finding_claim_little():
    # 2 decision points out of a CCN 3, 292-line method: the plain maximum of
    # the two shares claimed two thirds of a size finding for 16 lines.
    share = recovered_share("large_method", _Shape(3, 292), _Span(2, 16))
    assert share == pytest.approx(16 / 292)


def test_floor_drops_a_one_point_span_whatever_its_size():
    fn = _Shape(12, 80)
    assert not _worth_extracting(fn, _Span(1, 8), {"complex_method"})
    assert _worth_extracting(fn, _Span(2, 5), {"complex_method"})
    # A 137-line span lifting one decision point out of a large method
    # (registerChannelsCli) is no longer a size lift worth a plan.
    assert not _worth_extracting(_Shape(30, 180), _Span(1, 137), {"large_method"})


def test_a_helper_inheriting_the_finding_undiminished_is_not_worth_it():
    # CCN 13 -> helper CCN 13: the complex method moved, it did not split.
    assert not _worth_extracting(_Shape(13, 36), _Span(12, 30), {"complex_method"})
    # CCN 25 (critical) -> helper CCN 9 (medium): a real partial step.
    assert _worth_extracting(_Shape(25, 117), _Span(8, 40), {"complex_method"})


def test_detector_offers_the_next_best_span_when_the_best_misses_the_floor():
    fn = _first(_PROCESS)
    lmap = get_language_map("python")
    candidates = find_extractions(fn, lmap)
    worth = [c for c in candidates if _worth_extracting(fn, c, {"complex_method"})]
    assert worth
    findings = [_Finding("complex_method", "process", line_start=2, health_impact=1.5)]
    (s,) = ExtractMethodDetector().detect(_ctx(_PROCESS, findings))
    assert s.plan["span"] == {"start": worth[0].start_line, "end": worth[0].end_line}


def test_slice_nloc_counts_code_lines_not_comments():
    src = """
        def tally(items, limit):
            total = 0
            # Comments inside the span are documentation, not code to move.
            # They used to count toward the span's size and its credit.
            for x in items:
                # skip what is over the limit
                if x > limit:
                    total += limit
                else:
                    total += x
            total = min(total, limit * 10)
            total = max(total, 0)
            print(total)
            return total
        """
    fn = _first(src)
    lmap = get_language_map("python")
    # Spans ending with the loop (line 11), the ones holding the comments.
    loop = [
        c for c in find_extractions(fn, lmap) if c.end_line == 11 and c.end_line - c.start_line >= 5
    ]
    assert loop
    assert all(c.slice_nloc <= c.end_line - c.start_line + 1 - 2 for c in loop)


def test_confidence_high_needs_a_real_share():
    findings = [_Finding("complex_method", "process", line_start=2, health_impact=1.5)]
    fn = _first(_PROCESS)
    (s,) = ExtractMethodDetector().detect(_ctx(_PROCESS, findings))
    assert s.confidence == "high"
    # The same span out of a much larger function moves almost nothing.
    fn.ccn = 250
    ctx = RefactoringContext(
        file_path="m.py", language="python", nloc=100, findings=findings, function_analyses=[fn]
    )
    (s,) = ExtractMethodDetector().detect(ctx)
    assert s.confidence == "medium"


def _parse(lang: str, src: str):
    try:
        from tree_sitter import Parser

        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter missing")
    grammar = _get_language(lang)
    if grammar is None:
        pytest.skip(f"tree-sitter language pack missing for {lang}")
    root = Parser(grammar).parse(textwrap.dedent(src).encode()).root_node
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "function_declaration":
            return node
        stack.extend(node.children)
    raise AssertionError("no function")


def test_jsx_prop_plumbing_is_not_offered_an_extraction():
    # TriageTab's shape: the branching is conditional spreads and template
    # ternaries wiring props, not logic a helper would simplify.
    node = _parse(
        "tsx",
        """
        function Tab({ id, scope, counts, lens }) {
          const key = `${id}${scope ? `:${scope}` : ""}`;
          return (
            <View
              k={key}
              {...(scope ? { scope } : {})}
              {...(counts ? { counts } : {})}
              {...(lens ? { lens } : {})}
            />
          );
        }
        """,
    )
    assert jsx_plumbing_dominates(node, get_language_map("tsx"))


def test_component_with_real_logic_is_still_eligible():
    node = _parse(
        "tsx",
        """
        function Lede({ a, b, items }) {
          let n = 0;
          for (const x of items) {
            if (x > a) {
              n += 1;
            } else if (x < b) {
              n -= 1;
            }
          }
          return <p>{n > 0 && <b>{n}</b>}</p>;
        }
        """,
    )
    assert not jsx_plumbing_dominates(node, get_language_map("tsx"))
    # No JSX at all: never gated, whatever the shape.
    plain = _parse("typescript", "function f(a) { return a ? { ...(a && { a }) } : {}; }")
    assert not jsx_plumbing_dominates(plain, get_language_map("typescript"))


def test_spreads_and_conditionals_outside_the_markup_are_logic():
    # The same shapes the gate reads as wiring inside JSX are ordinary logic
    # in the component body: a config object built before the return.
    node = _parse(
        "tsx",
        """
        function Panel({ defaults, a, b, c, d }) {
          const cfg = { ...defaults, x: a && b, y: c ? d : null, z: c || d };
          return <View cfg={cfg} />;
        }
        """,
    )
    assert not jsx_plumbing_dominates(node, get_language_map("tsx"))


def test_a_best_span_below_the_floor_yields_to_the_next_one(monkeypatch):
    from repowise.core.analysis.health.dataflow import Extraction
    from repowise.core.analysis.health.refactoring import extract_method

    trivial = Extraction(10, 17, ("a",), (), slice_nloc=8, ccn_removed=1)
    worth = Extraction(20, 27, ("b",), (), slice_nloc=8, ccn_removed=3)
    monkeypatch.setattr(extract_method, "find_extractions", lambda _a, _l, _r=None, _p=None: [trivial, worth])
    fn = _Shape(12, 40)
    analysis = type(
        "A", (), {"name": "f", "start_line": 1, "end_line": 50, "ccn": 12, "nloc": 40,
                  "fn_node": None}
    )()
    assert not _worth_extracting(fn, trivial, {"complex_method"})
    ctx = RefactoringContext(
        file_path="m.py",
        language="python",
        nloc=100,
        findings=[_Finding("complex_method", "f", 1, 1.2)],
        function_analyses=[analysis],
    )
    (s,) = ExtractMethodDetector().detect(ctx)
    assert s.plan["span"] == {"start": 20, "end": 27}
    assert s.impact_delta == round(1.2 * 3 / 12, 3)


# A plan that names the extracted loop's own counter as a parameter cannot
# compile: the call site has no such variable. Two loops sharing ``i`` used to
# produce exactly that, because the first loop's ``i`` looked like a definition
# before the span and the header's same-line read looked like an input.
_TWO_LOOPS_JAVA = """
class Demo {
    int run(int[] a, int n) {
        int sum = 0;
        for (int i = 0; i < n; i++) {
            sum += a[i];
        }
        int total = 0;
        for (int i = 0; i < n; i++) {
            if (a[i] > 0) {
                total += a[i];
            } else {
                total -= a[i];
            }
        }
        return sum + total;
    }
}
"""


def test_plan_params_leave_out_a_loop_counter_the_span_declares():
    res = analyze_file("Demo.java", "java", _TWO_LOOPS_JAVA.encode(), flagged_only=False)
    if res.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for java")
    ctx = RefactoringContext(
        file_path="Demo.java",
        language="java",
        nloc=100,
        findings=[_Finding("complex_method", "run", line_start=3, health_impact=1.5)],
        function_analyses=res.functions,
    )

    suggestions = ExtractMethodDetector().detect(ctx)

    assert len(suggestions) == 1
    plan = suggestions[0].plan
    assert plan["params"] == ["a", "n"]
    assert plan["returns"] == ["total"]


def test_a_span_holding_nearly_the_whole_body_is_not_a_split():
    # Everything but the closing return: lifting it would leave a function that
    # only calls the helper, the smell moved under a new name.
    lmap = get_language_map("python")
    src = """
        def tally(items, limit):
            total = 0
            seen = 0
            for x in items:
                if x > limit:
                    total += limit
                else:
                    total += x
                seen += 1
            return total
        """
    assert find_extractions(_first(src), lmap) == []


def test_a_span_leaving_real_work_behind_is_still_offered():
    lmap = get_language_map("python")
    fn = _first(_PROCESS)
    (best, *_) = find_extractions(fn, lmap)
    assert "average" in best.returns
    assert best.end_line < fn.end_line


# -- offer gates (span share, size, docstring start) ---------------------------------


def _fn_lines(start: int, end: int):
    return type("A", (), {"start_line": start, "end_line": end, "fn_node": None})()


def _offer(fn_span: tuple[int, int], span: tuple[int, int], nloc: int, ccn: int = 9) -> bool:
    from repowise.core.analysis.health.dataflow import Extraction
    from repowise.core.analysis.health.refactoring.extract_method import _offerable

    return _offerable(_fn_lines(*fn_span), Extraction(*span, (), (), nloc, ccn))


@pytest.mark.parametrize(
    ("fn_span", "span", "nloc"),
    [
        ((31, 313), (108, 311), 183),  # parseLineDirectives, share 0.72
        ((1601, 1827), (1665, 1826), 151),  # cmd_migrate, share 0.71
        ((18, 63), (29, 57), 23),  # buildConformanceOverlay, share 0.63
    ],
)
def test_a_span_over_three_fifths_of_the_function_is_not_offered(fn_span, span, nloc):
    assert not _offer(fn_span, span, nloc)


def test_a_six_line_span_is_not_offered():
    # bulk_update: 6 code lines out of a 132-line function.
    assert not _offer((1304, 1435), (1349, 1354), 6, ccn=3)
    assert _offer((1304, 1435), (1349, 1356), 8, ccn=3)


@pytest.mark.parametrize(
    ("fn_span", "span", "nloc"),
    [
        ((171, 210), (186, 209), 24),  # extract_include_dirs, share exactly 0.600
        ((1699, 2208), (1769, 1786), 12),  # compute_order_by_sql
        ((273, 367), (326, 366), 37),  # _start_multiplex
        ((170, 216), (183, 204), 21),  # parallel provider search
        ((20, 78), (38, 59), 22),  # qwen-oauth prepare_messages
    ],
)
def test_calibration_shapes_are_still_offered(fn_span, span, nloc):
    assert _offer(fn_span, span, nloc)


# uninstall_gui: the slicer's best span ties with the one a line lower and wins
# on the earlier start, so it opens on the docstring.
_DOCSTRING_FIRST = """
def uninstall(home, packaged, keep):
    \"\"\"Remove the built artifacts and the packaged app.

    Never touches user data. Returns the paths removed.
    \"\"\"
    removed = []
    for path in home:
        if path.exists() and path.is_dir():
            removed.append(path)
    for path in packaged:
        if path.exists() or path.is_symlink():
            removed.append(path)
    print(len(removed))
    if not removed:
        print("nothing to remove")
        return []
    if keep:
        print("kept user data")
        return removed
    print("removed user data")
    print("done")
    print("bye")
    return removed
"""


def test_a_span_opening_on_the_docstring_is_not_offered():
    fn = _first(_DOCSTRING_FIRST)
    (best, *_) = find_extractions(fn, get_language_map("python"))
    assert best.start_line == fn.start_line + 1  # the docstring line
    findings = [_Finding("complex_method", "uninstall", fn.start_line, 1.5)]
    assert ExtractMethodDetector().detect(_ctx(_DOCSTRING_FIRST, findings)) == []


def test_a_leading_comment_is_not_a_docstring():
    from repowise.core.analysis.health.refactoring.extract_method import _starts_on_docstring

    node = _parse(
        "typescript",
        """
        function f(a: number[]) {
          /** Totals the positive values. */
          let total = 0;
          return total;
        }
        """,
    )
    assert not _starts_on_docstring(node, node.start_point[0] + 2)
    assert not _starts_on_docstring(node, node.start_point[0] + 3)
    strict = _parse("typescript", 'function g() {\n  "use strict";\n  let x = 1;\n  return x;\n}\n')
    assert _starts_on_docstring(strict, strict.start_point[0] + 2)


def test_a_comment_before_the_docstring_does_not_hide_it():
    src = _DOCSTRING_FIRST.replace(
        "def uninstall(home, packaged, keep):\n",
        "def uninstall(home, packaged, keep):\n    # kept in sync with the installer\n",
    )
    fn = _first(src)
    (best, *_) = find_extractions(fn, get_language_map("python"))
    assert best.start_line == fn.start_line + 2  # the docstring, under the comment
    findings = [_Finding("complex_method", "uninstall", fn.start_line, 1.5)]
    assert ExtractMethodDetector().detect(_ctx(src, findings)) == []


def test_a_refused_span_yields_only_to_a_disjoint_one(monkeypatch):
    from repowise.core.analysis.health.dataflow import Extraction
    from repowise.core.analysis.health.refactoring import extract_method

    whole = Extraction(2, 80, ("a",), (), slice_nloc=60, ccn_removed=9)  # share 0.79
    shrunk = Extraction(2, 55, ("a",), (), slice_nloc=40, ccn_removed=8)  # overlaps it
    disjoint = Extraction(85, 95, ("b",), (), slice_nloc=10, ccn_removed=3)
    monkeypatch.setattr(
        extract_method, "find_extractions", lambda _a, _l, _r=None, _p=None: [whole, shrunk, disjoint]
    )
    analysis = type(
        "A", (), {"name": "f", "start_line": 1, "end_line": 100, "ccn": 30, "nloc": 90,
                  "fn_node": None}
    )()
    ctx = RefactoringContext(
        file_path="m.py",
        language="python",
        nloc=100,
        findings=[_Finding("complex_method", "f", 1, 1.2)],
        function_analyses=[analysis],
    )
    (s,) = ExtractMethodDetector().detect(ctx)
    assert s.plan["span"] == {"start": 85, "end": 95}


def _span_in_out(src: str, s: int, e: int):
    from repowise.core.analysis.health.dataflow import slice as slicer

    fn = _first(src)
    def_lines, use_lines = slicer._var_lines(fn.def_use)
    declared_first = slicer._declared_before_read(fn.def_use)
    reads = slicer._reads_observing(fn, def_lines)
    return slicer._infer_in_out(def_lines, use_lines, s, e, declared_first, reads=reads)


def test_a_comprehension_binder_is_not_a_parameter():
    # Seen on get_health_findings: an earlier comprehension's ``v`` made the
    # span's own ``v`` an input, and the call passed a name the caller lacks.
    src = """
    def f(severity, order, t):
        exact = [v.strip() for v in severity if v]
        b = len(exact)
        allowed = [k for k, v in order.items() if v >= t]
        return allowed, b
    """
    assert _span_in_out(src, 4, 5) == (("exact", "order", "t"), ("allowed", "b"))


def test_an_import_path_naming_a_parameter_is_not_a_read():
    # Seen on _lifespan: ``from repowise.server...`` read its ``server`` parameter.
    src = """
    def f(server, root):
        a = root / "x"
        from repowise.server.enrich import Enricher
        e = Enricher(a)
        return e
    """
    assert _span_in_out(src, 4, 5) == (("a",), ("e",))


def test_a_span_holding_an_import_read_after_it_is_refused():
    # The import would leave with the helper; an imported name is never made
    # an input or output (the helper re-imports it).
    src = """
    def f(a, c):
        from pkg.mod import Thing
        if c:
            a += 1
        t = Thing(a)
        return t
    """
    with pytest.MonkeyPatch.context() as mp:
        from repowise.core.analysis.health.dataflow import slice as slicer

        mp.setattr(slicer, "_MAX_BODY_SHARE", float("inf"))
        mp.setattr(slicer, "_MIN_SLICE_NLOC", 1)
        spans = find_extractions(_first(src), get_language_map("python"))
    found = {(x.start_line, x.end_line): (x.params, x.returns) for x in spans}
    assert (3, 5) not in found
    assert found[(4, 6)] == (("a", "c"), ("t",))
