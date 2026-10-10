"""Where an Extract Method helper's ``suggested_name`` comes from, and when it
has none: a ``timed()`` stage label, a banner comment, or ``compute_<out>`` for
an effect-free span, never a name a sibling in the helper's scope already uses.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import Extraction, analyze_file
from repowise.core.analysis.health.refactoring.extract_method import (
    ExtractMethodDetector,
    helper_name,
)
from repowise.core.analysis.health.refactoring.models import RefactoringContext


@dataclass
class _Finding:
    biomarker_type: str
    function_name: str
    line_start: int
    health_impact: float


def _functions(language: str, ext: str, src: str):
    from repowise.core.ingestion.parser import _get_language

    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    res = analyze_file(f"m.{ext}", language, textwrap.dedent(src).encode(), flagged_only=False)
    if res.stats.functions_seen == 0:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return res.functions


def _names(src: str, language: str = "python", ext: str = "py") -> list[tuple[str, str | None]]:
    """``(function, suggested_name)`` per plan, every function flagged."""
    fns = _functions(language, ext, src)
    ctx = RefactoringContext(
        file_path=f"m.{ext}",
        language=language,
        nloc=100,
        findings=[_Finding("complex_method", f.name, f.start_line, 1.5) for f in fns],
        function_analyses=fns,
    )
    plans = sorted(ExtractMethodDetector().detect(ctx), key=lambda s: s.line_start or 0)
    return [(s.target_symbol, s.plan["suggested_name"]) for s in plans]


def _name(src: str, language: str = "python", ext: str = "py") -> str | None:
    (only,) = _names(src, language, ext)
    return only[1]


# The span the detector lifts is the threshold loop through ``average``; the
# ``{head}`` and ``{effect}`` slots put a comment above it and a statement in it.
_PY = """
def process(records, threshold):
    results = []
    errors = 0
    for r in records:
        if r is None:
            errors += 1
            continue
        results.append(r)
{head}    total = 0
    count = 0
    for v in results:
        if v > threshold:
            total += v
            count += 1
{effect}        else:
            total -= v
    average = total / count if count else 0
    return average, errors
"""


def _py(head: str = "", effect: str = "") -> str:
    return _PY.format(head=head, effect=effect)


# -- sources in order -------------------------------------------------------------


def test_without_a_banner_the_single_out_value_names_an_effect_free_span():
    assert _name(_py()) == "compute_average"


def test_a_banner_comment_above_the_span_names_the_helper():
    assert _name(_py(head="    # Sum values above threshold\n")) == "sum_values_above_threshold"


@pytest.mark.parametrize(
    "head",
    [
        "    # ---- Sum values ----\n",
        "    # ===========\n    # Sum values\n    # ===========\n",
        "    # 2. Sum values\n",
        "    # Sum values (above the bar).\n",
    ],
)
def test_rules_numbering_and_asides_around_a_banner_are_dropped(head):
    assert _name(_py(head=head)) == "sum_values"


@pytest.mark.parametrize(
    "head",
    [
        # Prose, not a name: too long, two lines, code in it, a tool directive,
        # a one-word heading.
        "    # Sum every value that clears the threshold and count them here\n",
        "    # Sum values above\n    # the threshold.\n",
        "    # total -> sum of values\n",
        "    # TODO sum values\n",
        "    # Totals\n",
        # Not touching the statement: it heads something else.
        "    # Sum values above threshold\n\n",
    ],
)
def test_a_comment_that_is_not_a_banner_leaves_the_out_value_name(head):
    assert _name(_py(head=head)) == "compute_average"


def test_a_span_holding_a_second_banner_takes_neither():
    src = _py(head="    # Sum values\n").replace(
        "    average = total", "    # Average them\n    average = total"
    )
    assert _name(src) == "compute_average"


def test_a_timed_stage_label_names_the_helper():
    src = """
    def persist(session, pages, timings, flags):
        written = 0
        for p in pages:
            if p is None:
                continue
            written += 1
        with timed(timings, "persist.pages"):
            for page in pages:
                if page.stale:
                    session.delete(page)
                elif page.new:
                    session.add(page)
                else:
                    session.merge(page)
            if flags:
                session.flush()
        return written
    """
    assert _name(src) == "persist_pages"


# -- no compute_ for a span with effects ----------------------------------------


@pytest.mark.parametrize(
    "effect",
    [
        "            print(v)\n",  # I/O
        "            records.append(v)\n",  # mutates a parameter
        "            threshold.seen = v\n",  # writes through a parameter
        "            sink[v] = count\n",  # writes into a name the span does not bind
    ],
)
def test_a_span_with_outside_effects_is_not_named_compute(effect):
    assert _name(_py(effect=effect)) is None


def test_a_banner_saying_compute_does_not_name_a_span_with_effects():
    assert _name(_py(head="    # Compute the totals\n", effect="            print(v)\n")) is None


def test_a_banner_still_names_a_span_with_effects():
    src = _py(head="    # Report the totals\n", effect="            print(v)\n")
    assert _name(src) == "report_totals"


def test_mutating_a_list_the_span_built_is_not_an_effect():
    src = (
        _py()
        .replace("    count = 0\n", "    count = 0\n    kept = []\n")
        .replace("            count += 1\n", "            count += 1\n            kept.append(v)\n")
    )
    assert _name(src) == "compute_average"


def test_writing_through_self_is_an_effect():
    src = """
    class Stats:
        def process(self, records, threshold):
            results = []
            for r in records:
                if r is None:
                    continue
                results.append(r)
            total = 0
            count = 0
            for v in results:
                if v > threshold:
                    total += v
                    self.count = count
                else:
                    total -= v
            average = total / count if count else 0
            return average
    """
    assert _name(src) is None


# -- collisions with the helper's scope ------------------------------------------


@pytest.mark.parametrize("sibling", ["compute_average", "_compute_average"])
def test_a_name_a_sibling_function_already_has_is_dropped(sibling):
    src = _py() + f"\n\ndef {sibling}(values):\n    return sum(values) / len(values)\n"
    assert _names(src) == [("process", None)]


def test_two_plans_in_one_scope_do_not_share_a_name():
    second = textwrap.dedent(_py()).replace("def process(", "def process_again(")
    assert _names(_py() + "\n" + second) == [
        ("process", "compute_average"),
        ("process_again", None),
    ]


def test_a_method_does_not_collide_with_a_module_function():
    method = textwrap.indent(textwrap.dedent(_py()).replace("(records,", "(self, records,"), "    ")
    src = "def compute_average(values):\n    return 0\n\n\nclass Stats:\n" + method
    assert _names(src) == [("process", "compute_average")]


# -- casing --------------------------------------------------------------------


_TS = """
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
    // Sum values above threshold
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


def test_a_banner_name_follows_the_files_casing():
    assert _name(_TS, "typescript", "ts") == "sumValuesAboveThreshold"


def test_the_out_value_name_keeps_the_files_casing():
    assert _name(_TS.replace("    // Sum values above threshold\n", ""), "typescript", "ts") == (
        "computeAverage"
    )


# -- the dogfood shapes ----------------------------------------------------------


def _span_name(language, ext, src, start, end, params, returns, *, needs_async=False):
    (fn,) = _functions(language, ext, src)
    first = fn.start_line - 1
    extraction = Extraction(
        first + start, first + end, tuple(params), tuple(returns), 8, 2, needs_async
    )
    return helper_name(fn, extraction, get_language_map(language), language)


def test_status_header_span_is_not_compute_deep():
    # hermes_cli/status.py:133: reads a flag, then prints the header.
    src = """
    def show_status(args):
        deep = getattr(args, 'deep', False)
        print()
        print(color("Hermes Agent Status", Colors.CYAN))
        line = _estop_status_line()
        if line:
            print()
            print(color(line, Colors.YELLOW))
        if deep and line:
            print("deep")
        return deep
    """
    assert _span_name("python", "py", src, 2, 10, ["args"], ["deep"]) is None


def test_run_main_span_is_not_compute_is_gateway_run_invocation():
    # openclaw src/cli/run-main.ts:640: marks a trace and asserts the runtime.
    src = """
    async function runCli(argv: string[], startupTrace: Trace) {
        const normalizedArgv = normalize(argv);
        const isGatewayRunInvocation = isGatewayRunInvocationArgv(normalizedArgv);
        startupTrace.mark("argv");
        assertSupportedRuntime();
        if (isGatewayRunInvocation || shouldLoad()) {
            loadCliDotEnv({ quiet: true });
        }
        return isGatewayRunInvocation;
    }
    """
    params, returns = ["normalizedArgv", "startupTrace"], ["isGatewayRunInvocation"]
    name = _span_name("typescript", "ts", src, 3, 8, params, returns)
    assert name is None


def test_gateway_child_span_is_not_compute_message():
    # openclaw extensions/qa-lab/src/gateway-child.ts:1119: closes streams.
    src = """
    async function startQaGatewayChild(stdoutLog: Stream, keepTemp: boolean, error: unknown) {
        await closeWriteStream(stdoutLog);
        if (!keepTemp) {
            await cleanupQaGatewayTempRoots({ stdoutLog });
        }
        const message = keepTemp ? appendRoot(format(error)) : format(error);
        return message;
    }
    """
    params = ["stdoutLog", "keepTemp", "error"]
    name = _span_name("typescript", "ts", src, 2, 6, params, ["message"], needs_async=True)
    assert name is None


def test_order_by_span_keeps_compute_order_by_sql():
    # hermes hermes_state_search.py:1769: pure, one product.
    src = """
    def _search(sort):
        if isinstance(sort, str):
            sort_norm = sort.strip().lower()
            if sort_norm not in ("newest", "oldest"):
                sort_norm = None
        else:
            sort_norm = None
        if sort_norm == "newest":
            order_by_sql = "ORDER BY m.timestamp DESC, rank"
        elif sort_norm == "oldest":
            order_by_sql = "ORDER BY m.timestamp ASC, rank"
        else:
            order_by_sql = "ORDER BY rank"
        return order_by_sql
    """
    assert _span_name("python", "py", src, 2, 13, ["sort"], ["order_by_sql"]) == (
        "compute_order_by_sql"
    )
