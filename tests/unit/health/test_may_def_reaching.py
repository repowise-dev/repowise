"""A write that may not happen hides nothing from the reads after it.

A ``switch`` / ``match`` is one CFG statement, so a write inside an arm sits in
the same basic block as the statements that follow. Reaching definitions used
to treat it as certain: the read after it observed the arm's write alone, the
earlier value was killed, and Extract Method offered a span that updated the
variable without returning it.

Each shape here has an ``if`` twin, where the CFG has the branch and the
answer was already right.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import analyze_file, find_extractions
from repowise.core.analysis.health.dataflow.defuse import Definition
from repowise.core.analysis.health.dataflow.reaching import _observe, observed_definitions
from repowise.core.analysis.health.refactoring.extract_method import ExtractMethodDetector
from repowise.core.analysis.health.refactoring.models import RefactoringContext

# One function per language whose dialect marks an arm's write: a loop that raises ``best`` and counts ``seen``,
# then ``{COND}`` which may overwrite ``best``, then a read of both. The span
# from ``seen = 0`` through the loop writes two values the code reads after
# it, so no helper with one return can replace it.
_JAVA = '''class A {
  int f(int v, int[] xs) {
    int best = -1;
    int seen = 0;
    for (int a : xs) {
      seen++;
      if (a > best) {
        best = a;
      } else {
        System.out.println(a);
      }
    }
    {COND}
    return best + seen;
  }
}
'''
_RUST = '''fn f(v: i32, xs: &[i32]) -> i32 {
    let mut best = -1;
    let mut seen = 0;
    for a in xs {
        seen += 1;
        if *a > best {
            best = *a;
        } else {
            println!("{}", a);
        }
    }
    {COND}
    return best + seen;
}
'''
_CPP = '''int f(int v, const std::vector<int>& xs) {
  int best = -1;
  int seen = 0;
  for (int a : xs) {
    seen++;
    if (a > best) {
      best = a;
    } else {
      log(a);
    }
  }
  {COND}
  return best + seen;
}
'''
_CSHARP = '''class A {
  int F(int v, int[] xs) {
    int best = -1;
    int seen = 0;
    foreach (int a in xs) {
      seen++;
      if (a > best) {
        best = a;
      } else {
        System.Console.WriteLine(a);
      }
    }
    {COND}
    return best + seen;
  }
}
'''

# language -> (file name, source, the arm form, its ``if`` twin)
_SHAPES: dict[str, tuple[str, str, str, str]] = {
    "java": (
        "A.java",
        _JAVA,
        "switch (v) {\n      case 1:\n        best = 0;\n        break;\n    }",
        "if (v == 1) {\n      best = 0;\n    }",
    ),
    "rust": (
        "a.rs",
        _RUST,
        "match v {\n        1 => { best = 0; }\n        _ => {}\n    }",
        "if v == 1 {\n        best = 0;\n    }",
    ),
    "csharp": (
        "A.cs",
        _CSHARP,
        "switch (v) {\n      case 1:\n        best = 0;\n        break;\n    }",
        "if (v == 1) {\n      best = 0;\n    }",
    ),
    "cpp": (
        "a.cpp",
        _CPP,
        "switch (v) {\n    case 1:\n      best = 0;\n      break;\n  }",
        "if (v == 1) {\n    best = 0;\n  }",
    ),
}
_LANGUAGES = sorted(_SHAPES)


@dataclass
class _Finding:
    biomarker_type: str
    function_name: str
    line_start: int
    health_impact: float


def _analyses(language: str, path: str, source: str):
    result = analyze_file(path, language, source.encode(), flagged_only=False)
    if result.stats.functions_seen == 0:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    assert result.functions, f"{language}: no function analysed"
    return result.functions


def _shape(language: str, *, arm: bool):
    path, template, arm_form, if_form = _SHAPES[language]
    source = template.replace("{COND}", arm_form if arm else if_form)
    return path, source, _analyses(language, path, source)[0]


def _line_of(source: str, text: str) -> int:
    hits = [i for i, row in enumerate(source.splitlines(), start=1) if text in row]
    assert len(hits) == 1, f"{text!r} is on lines {hits}"
    return hits[0]


def _best_writes(language: str, source: str) -> tuple[int, int, int]:
    """The lines of the three writes of ``best``: the first, the loop's and
    the arm's."""
    loop = "best = *a" if language == "rust" else "best = a"
    return _line_of(source, "best = -1"), _line_of(source, loop), _line_of(source, "best = 0")


def _observed_lines(analysis, name: str, line: int) -> set[int]:
    """The lines of the definitions a read of *name* on *line* may observe."""
    defs = analysis.reaching.definitions
    seen: set[int] = set()
    found = False
    for use, indices in observed_definitions(analysis.def_use, analysis.reaching):
        if use.name == name and use.line == line and not use.echo:
            found = True
            seen.update(defs[i].line for i in indices)
    assert found, f"no read of {name} on line {line}"
    return seen


def _definition(var: str, line: int, index: int, *, may: bool = False) -> Definition:
    return Definition(var=var, block_id=3, index=index, line=line, may=may)


# -- _observe: what a read sees among its own block's definitions --------------


def test_a_read_looks_back_through_a_may_def_to_the_certain_one():
    local = [_definition("x", 2, 0), _definition("x", 5, 1, may=True)]
    assert sorted(_observe(local, entering=[9], line=7)) == [0, 1]


def test_a_read_after_only_may_defs_also_sees_what_enters_the_block():
    local = [_definition("x", 4, 0, may=True), _definition("x", 5, 1, may=True)]
    assert sorted(_observe(local, entering=[9], line=7)) == [0, 1, 9]


def test_a_certain_def_after_a_may_def_hides_it():
    local = [
        _definition("x", 2, 0),
        _definition("x", 4, 1, may=True),
        _definition("x", 6, 2),
    ]
    assert _observe(local, entering=[9], line=7) == [2]


def test_a_certain_def_still_hides_the_one_before_it():
    """The control: nothing here may be skipped, so the last write wins."""
    local = [_definition("x", 2, 0), _definition("x", 5, 1)]
    assert _observe(local, entering=[9], line=7) == [1]


def test_every_def_on_the_latest_certain_line_is_seen_and_nothing_before_it():
    """Lines cannot order two writes that share one, so both count."""
    local = [
        _definition("x", 2, 0),
        _definition("x", 5, 1),
        _definition("x", 5, 2, may=True),
        _definition("x", 5, 3),
    ]
    assert sorted(_observe(local, entering=[9], line=7)) == [1, 2, 3]


def test_a_read_before_every_def_sees_only_what_enters():
    local = [_definition("x", 8, 0, may=True)]
    assert _observe(local, entering=[9], line=7) == [9]


def test_a_def_on_the_reads_own_line_is_still_counted():
    local = [_definition("x", 2, 0), _definition("x", 7, 1, may=True)]
    assert sorted(_observe(local, entering=[9], line=7)) == [0, 1]


# -- the definitions themselves -------------------------------------------------


@pytest.mark.parametrize("language", _LANGUAGES)
def test_a_write_in_an_arm_is_a_may_def(language):
    _path, source, analysis = _shape(language, arm=True)
    first, loop, arm = _best_writes(language, source)
    writes = {d.line: d.may for d in analysis.def_use.definitions if d.var == "best"}
    assert writes == {first: False, loop: False, arm: True}


@pytest.mark.parametrize("language", _LANGUAGES)
def test_a_write_in_an_if_branch_is_a_certain_def_of_its_own_block(language):
    _path, source, analysis = _shape(language, arm=False)
    first, loop, arm = _best_writes(language, source)
    writes = {d.line: d.may for d in analysis.def_use.definitions if d.var == "best"}
    assert writes == {first: False, loop: False, arm: False}


# A name an arm declares is a new binding for the rest of that arm, not a
# write to the variable the code before the ``switch`` set. Arms reuse such
# names, so these stay certain: the read in an arm sees its own declaration.
_DECLARED_IN_AN_ARM: dict[str, tuple[str, str, str]] = {
    "java": (
        "A.java",
        """class A {
  int f(int v) {
    int out = 0;
    switch (v) {
      case 1: {
        int n = 1;
        out = n;
        break;
      }
      case 2: {
        int n = 2;
        out = n + 1;
        break;
      }
    }
    return out;
  }
}
""",
        "out = n + 1",
    ),
    "rust": (
        "a.rs",
        """fn f(v: Option<i32>, w: Option<i32>) -> i32 {
    let mut out = 0;
    match v {
        Some(n) => {
            out = n;
        }
        None => {}
    }
    match w {
        Some(n) => {
            out = n + 1;
        }
        None => {}
    }
    out
}
""",
        "out = n + 1",
    ),
}


@pytest.mark.parametrize("language", sorted(_DECLARED_IN_AN_ARM))
def test_a_name_an_arm_declares_is_not_a_may_def(language):
    path, source, second_read = _DECLARED_IN_AN_ARM[language]
    analysis = _analyses(language, path, source)[0]
    declared = [d for d in analysis.def_use.definitions if d.var == "n"]
    assert [d.may for d in declared] == [False, False]
    assert all(d.declares for d in declared)
    # The second arm's read sees its own ``n``, not the first arm's as well.
    read = _line_of(source, second_read)
    assert _observed_lines(analysis, "n", read) == {declared[1].line}
    # ``out`` is assigned, not declared, in both arms.
    assert [d.may for d in analysis.def_use.definitions if d.var == "out"] == [False, True, True]


def test_rust_if_let_binder_is_left_as_it_was():
    """An ``if let`` binder is a new variable for its arm, as a name an arm
    declares is. Marked as a write that may not happen, the second arm's read
    below would observe the first arm's binder too, and a span holding the
    first ``if let`` would seem to output ``limit``: ripgrep's
    ``matcher_rust`` lost six spans that way."""
    source = """fn f(a: Option<usize>, b: Option<usize>, out: &mut Vec<usize>) {
    if let Some(limit) = a {
        out.push(limit);
    }
    if let Some(limit) = b {
        out.push(limit + 1);
    }
}
"""
    analysis = _analyses("rust", "a.rs", source)[0]
    binders = [d for d in analysis.def_use.definitions if d.var == "limit"]
    assert [(d.line, d.may) for d in binders] == [(2, False), (5, False)]
    assert _observed_lines(analysis, "limit", 3) == {2}
    assert _observed_lines(analysis, "limit", 6) == {5}


def test_cpp_ternary_arm_write_is_a_may_def():
    """C++ keeps a conditional expression's arms inside one statement too."""
    source = """int f(int v, int w) {
  int best = w;
  int seen = v > 0 ? (best = 0) : 1;
  return best + seen;
}
"""
    analysis = _analyses("cpp", "a.cpp", source)[0]
    writes = {d.line: d.may for d in analysis.def_use.definitions if d.var == "best"}
    assert writes == {2: False, 3: True}
    assert _observed_lines(analysis, "best", 4) == {2, 3}
    # ``seen`` is assigned by the statement itself, whichever arm ran.
    assert [d.may for d in analysis.def_use.definitions if d.var == "seen"] == [False]


# -- reaching: the earlier value still reaches the read ------------------------


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_read_after_an_arm_observes_the_earlier_writes(language):
    _path, source, analysis = _shape(language, arm=True)
    observed = _observed_lines(analysis, "best", _line_of(source, "return best + seen"))
    assert observed == set(_best_writes(language, source))


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_if_twin_observes_the_same_writes(language):
    _path, source, analysis = _shape(language, arm=False)
    observed = _observed_lines(analysis, "best", _line_of(source, "return best + seen"))
    assert observed == set(_best_writes(language, source))


def test_the_earlier_write_reaches_a_read_in_a_later_block():
    """The arm's block must not kill it either: the read is past an ``if``."""
    source = '''class A {
  int f(int v, int w) {
    int best = w;
    switch (v) {
      case 1:
        best = 0;
        break;
    }
    if (w > 5) {
      w = 0;
    }
    return best + w;
  }
}
'''
    analysis = _analyses("java", "A.java", source)[0]
    assert _observed_lines(analysis, "best", 12) == {3, 6}


def test_a_certain_write_after_the_arm_kills_both():
    source = '''class A {
  int f(int v, int w) {
    int best = w;
    switch (v) {
      case 1:
        best = 0;
        break;
    }
    best = 7;
    if (w > 5) {
      w = 0;
    }
    return best + w;
  }
}
'''
    analysis = _analyses("java", "A.java", source)[0]
    assert _observed_lines(analysis, "best", 13) == {9}


# Three blocks: ``best`` is written in the first, ``{MIDDLE}`` sits alone in the
# join after an ``if``, and the read is in the join after a second ``if``. What
# the read observes is then decided by the middle block's GEN and KILL alone.
_THREE_BLOCKS = '''class A {
  int f(int v, int w) {
    int best = w;
    if (w > 5) {
      w = 0;
    }
    {MIDDLE}
    if (w > 1) {
      w = 1;
    }
    return best + w;
  }
}
'''


def test_a_block_holding_only_a_may_def_kills_nothing():
    source = _THREE_BLOCKS.replace(
        "{MIDDLE}", "switch (v) {\n      case 1:\n        best = 0;\n        break;\n    }"
    )
    analysis = _analyses("java", "A.java", source)[0]
    read = _line_of(source, "return best + w")
    assert _observed_lines(analysis, "best", read) == {3, _line_of(source, "best = 0")}


def test_a_block_holding_a_certain_def_kills_what_entered_it():
    """The control for the one above: the same place, a write that happens."""
    source = _THREE_BLOCKS.replace("{MIDDLE}", "best = 0;")
    analysis = _analyses("java", "A.java", source)[0]
    read = _line_of(source, "return best + w")
    assert _observed_lines(analysis, "best", read) == {_line_of(source, "best = 0")}


# -- the slicer and the detector -------------------------------------------------


def _spans_dropping_best(language: str, *, arm: bool) -> list[tuple[int, int]]:
    _path, _source, analysis = _shape(language, arm=arm)
    return [
        (x.start_line, x.end_line)
        for x in find_extractions(analysis, get_language_map(language))
        if "seen" in x.returns and "best" not in x.returns
    ]


@pytest.mark.parametrize("language", _LANGUAGES)
def test_no_span_returns_the_count_and_drops_the_value_the_arm_may_keep(language):
    assert _spans_dropping_best(language, arm=True) == []


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_if_twin_has_no_such_span_either(language):
    assert _spans_dropping_best(language, arm=False) == []


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_detector_offers_no_plan_for_the_arm_form(language):
    path, source, analysis = _shape(language, arm=True)
    ctx = RefactoringContext(
        file_path=path,
        language=language,
        nloc=100,
        findings=[_Finding("complex_method", analysis.name, analysis.start_line, 5.0)],
        function_analyses=_analyses(language, path, source),
    )
    assert [s.plan["span"] for s in ExtractMethodDetector().detect(ctx)] == []


def test_a_span_that_holds_the_arm_is_still_refused_when_the_value_is_read_after():
    """Unchanged behaviour: the arm inside the span is not a certain write, so
    the span cannot return ``best``."""
    _path, source, analysis = _shape("java", arm=True)
    arm_line = _line_of(source, "best = 0")
    holding = [
        x
        for x in find_extractions(analysis, get_language_map("java"))
        if x.start_line <= arm_line <= x.end_line
    ]
    assert holding == []
