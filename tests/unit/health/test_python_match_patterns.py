"""Python ``match`` in the dataflow layer (#3473).

The arms of a ``match`` are not statements, so Extract Method must not offer a
span made of ``case`` clauses. And a pattern binds the names it captures, so
those are definitions, not reads; the names it only refers to (a class, a
dotted value, a keyword) are not.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import analyze_file, find_extractions

KINDS = ["open", "close", "write", "flush", "sync", "seek", "tell", "stat"]
ARM = """        case ("{kind}", name, data):
            if data:
                out.{kind}(name, data)
            elif name:
                out.{kind}(name)
            else:
                log.warn("{kind}")
            log.info(name)
"""
BIG_MATCH = (
    "def handle(event, out, log):\n    log.info(event)\n    match event:\n"
    + "".join(ARM.format(kind=kind) for kind in KINDS)
    + "        case _:\n            log.warn(event)\n    out.sync()\n    return out\n"
)

PATTERNS = """def f(event):
    match event:
        case ("open", name, data):
            print(name, data)
        case Point(x=px, y=py) as pt:
            print(px, py, pt)
        case [a, *others]:
            print(a, others)
        case {"k": c, **kw}:
            print(c, kw)
        case Color.RED | other if other > 3:
            print(other)
        case _:
            pass
"""


def _analysis(source: str):
    result = analyze_file("m.py", "python", source.encode(), flagged_only=False)
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for python")
    (analysis,) = result.functions
    return analysis


def _case_lines(source: str) -> set[int]:
    return {
        i for i, row in enumerate(source.splitlines(), start=1) if row.lstrip().startswith("case ")
    }


def test_no_extraction_span_is_made_of_case_clauses() -> None:
    analysis = _analysis(BIG_MATCH)
    case_lines = _case_lines(BIG_MATCH)

    extractions = find_extractions(analysis, get_language_map("python"))

    assert extractions, "the arms' own bodies should still be candidates"
    assert not [x for x in extractions if x.start_line in case_lines]


def test_pattern_captures_are_definitions() -> None:
    analysis = _analysis(PATTERNS)

    defined = {d.var for d in analysis.def_use.definitions}

    assert {"name", "data", "px", "py", "pt", "a", "others", "c", "kw", "other"} <= defined
    # A keyword names an attribute, a class or dotted name is a value, ``_`` binds nothing.
    assert not defined & {"x", "y", "Point", "Color", "RED", "_"}


def test_pattern_value_names_are_reads() -> None:
    analysis = _analysis(PATTERNS)

    read = {u.name for block in analysis.def_use.blocks.values() for u in block.uses}

    assert {"Point", "Color"} <= read
    assert not read & {"x", "y", "RED"}
