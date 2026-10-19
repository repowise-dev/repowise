"""An Extract Method helper's ``suggested_name`` is a placeholder the agent
applying the plan renames: ``extracted_helper`` in the file's casing,
numbered when a name in the helper's scope already takes it.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

import pytest

from repowise.core.analysis.health.dataflow import analyze_file
from repowise.core.analysis.health.refactoring.extract_method import ExtractMethodDetector
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
    """``(function, suggested_name)`` per plan, every function flagged. The
    name is read before the private form (Python's ``_``), which
    ``test_extract_method_render`` pins."""
    fns = _functions(language, ext, src)
    ctx = RefactoringContext(
        file_path=f"m.{ext}",
        language=language,
        nloc=100,
        findings=[_Finding("complex_method", f.name, f.start_line, 1.5) for f in fns],
        function_analyses=fns,
    )
    plans = sorted(ExtractMethodDetector().detect(ctx), key=lambda s: s.line_start or 0)
    return [(s.target_symbol, _starting_name(s.plan["suggested_name"], language)) for s in plans]


def _starting_name(name: str | None, language: str) -> str | None:
    return name.removeprefix("_") if name and language == "python" else name


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



def test_the_helper_is_named_with_a_placeholder():
    assert _name(_py()) == "extracted_helper"


@pytest.mark.parametrize(
    "head",
    ["    # Sum values above threshold\n", '    with timed(t, "persist.pages"):\n        pass\n'],
)
def test_nothing_in_the_code_names_the_helper(head):
    # A banner, a stage label or the OUT value no longer name it.
    assert _name(_py(head=head)) == "extracted_helper"


# -- collisions with the helper's scope ------------------------------------------


@pytest.mark.parametrize("sibling", ["extracted_helper", "_extracted_helper"])
def test_a_placeholder_a_sibling_function_already_has_is_numbered(sibling):
    src = _py() + f"\n\ndef {sibling}(values):\n    return sum(values) / len(values)\n"
    assert _names(src) == [("process", "extracted_helper_2")]


def test_two_plans_in_one_scope_do_not_share_a_name():
    second = textwrap.dedent(_py()).replace("def process(", "def process_again(")
    assert _names(_py() + "\n" + second) == [
        ("process", "extracted_helper"),
        ("process_again", "extracted_helper_2"),
    ]


def test_a_method_does_not_collide_with_a_module_function():
    method = textwrap.indent(textwrap.dedent(_py()).replace("(records,", "(self, records,"), "    ")
    src = "def extracted_helper(values):\n    return 0\n\n\nclass Stats:\n" + method
    assert _names(src) == [("process", "extracted_helper")]


@pytest.mark.parametrize(
    "prefix",
    [
        "extracted_helper = None\n\n",  # a module-level assignment
        "from stats import extracted_helper\n\n",  # an import
        "from stats import mean as extracted_helper\n\n",
    ],
)
def test_module_assignments_and_imports_take_the_name(prefix):
    assert _names(prefix + _py()) == [("process", "extracted_helper_2")]


def test_the_host_functions_own_parameters_and_locals_take_the_name():
    src = _py().replace("(records, threshold)", "(records, threshold, extracted_helper=None)")
    assert _names(src) == [("process", "extracted_helper_2")]


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



_GO = """
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
    // Sum values above threshold
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

_RUST = """
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
    // Sum values above threshold
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


def test_the_placeholder_follows_the_files_casing():
    assert _name(_TS, "typescript", "ts") == "extractedHelper"
    assert _name(_GO, "go", "go") == "extractedHelper"
    assert _name(_RUST, "rust", "rs") == "extracted_helper"


def test_go_exported_and_unexported_spellings_collide():
    src = _GO + "\nfunc ExtractedHelper(xs []int) int {\n    return 0\n}\n"
    assert _names(src, "go", "go") == [("process", "extractedHelper2")]
