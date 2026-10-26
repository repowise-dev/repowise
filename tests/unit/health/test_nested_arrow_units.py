"""A named, block-bodied arrow function is a unit of its own, and a function's
NLOC leaves out the lines of the units nested in it (#4575).

Since #3281 a ``function`` nested in another function has its own entry and
its branches leave the enclosing function's CCN and nesting. An arrow function
bound to a name with a block body, ``const check = () => { ... }``, is the same
thing written another way, but it rolled up into its enclosing function, and a
function's NLOC still counted the lines of every unit nested in it. Inline
callbacks and expression-bodied arrows still roll up.

Every nested body here is three nested ``if``s: CCN 4, nesting 3.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

_C3 = "if (a) { if (b) { if (c) { } } }"


def _walk(path: str, language: str, source: str):
    try:
        from repowise.core.ingestion.parser import _get_language, grammar_tag_for
    except ImportError:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if _get_language(grammar_tag_for(language, path)) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return walk_file(path, language, source.encode())


def _by_name(fcx) -> dict:
    names = [fc.name for fc in fcx.functions]
    assert len(names) == len(set(names)), names
    return {fc.name: fc for fc in fcx.functions}


def _shape(fcx) -> dict[str, tuple[int, int]]:
    return {name: (fc.ccn, fc.max_nesting) for name, fc in _by_name(fcx).items()}


# (id, path, language, source, name of the nested unit)
_UNITS = [
    (
        "const",
        "a.js",
        "javascript",
        f"function outer(){{ const check = () => {{ {_C3} }}; }}\n",
        "check",
    ),
    (
        "let-async",
        "a.js",
        "javascript",
        f"function outer(){{ let check = async (x) => {{ {_C3} }}; }}\n",
        "check",
    ),
    (
        "assignment",
        "a.js",
        "javascript",
        f"function outer(){{ api.check = () => {{ {_C3} }}; }}\n",
        "api.check",
    ),
    (
        "object-pair",
        "a.js",
        "javascript",
        f"function outer(){{ return {{ check: () => {{ {_C3} }} }}; }}\n",
        "check",
    ),
    (
        "object-pair-string-key",
        "a.js",
        "javascript",
        f"function outer(){{ return {{ 'check': () => {{ {_C3} }} }}; }}\n",
        "check",
    ),
    (
        "parenthesized",
        "a.js",
        "javascript",
        f"function outer(){{ const check = (() => {{ {_C3} }}); }}\n",
        "check",
    ),
    (
        "typescript-as",
        "a.ts",
        "typescript",
        f"function outer(){{ const check = (() => {{ {_C3} }}) as Check; }}\n",
        "check",
    ),
    (
        "typescript-satisfies",
        "a.ts",
        "typescript",
        f"function outer(){{ const check = (() => {{ {_C3} }}) satisfies Check; }}\n",
        "check",
    ),
    (
        "inside-an-arrow-wrapper",
        "a.js",
        "javascript",
        f"(() => {{ const check = () => {{ {_C3} }}; check(); }})();\n",
        "check",
    ),
    (
        "inside-a-method",
        "a.ts",
        "typescript",
        f"class A {{ outer() {{ const check = (n: number): void => {{ {_C3} }}; }} }}\n",
        "check",
    ),
]


@pytest.mark.parametrize(
    ("path", "language", "source", "nested"),
    [pytest.param(*row[1:], id=row[0]) for row in _UNITS],
)
def test_a_named_block_bodied_arrow_is_a_unit_of_its_own(path, language, source, nested):
    shape = _shape(_walk(path, language, source))

    outer = next(name for name in shape if name != nested)
    assert shape == {outer: (1, 0), nested: (4, 3)}


# (id, source): the arrow stays with ``outer``.
_ROLL_UP = [
    ("inline-callback", f"function outer(){{ items.forEach((x) => {{ {_C3} }}); }}\n"),
    ("callback-in-parentheses", f"function outer(){{ run((() => {{ {_C3} }})); }}\n"),
    ("returned", f"function outer(){{ return () => {{ {_C3} }}; }}\n"),
    ("array-element", f"function outer(){{ return [() => {{ {_C3} }}]; }}\n"),
    ("parameter-default", f"function outer(cb = () => {{ {_C3} }}){{ }}\n"),
]


@pytest.mark.parametrize(("source",), [pytest.param(row[1], id=row[0]) for row in _ROLL_UP])
def test_an_arrow_that_is_not_bound_to_a_name_rolls_up(source):
    shape = _shape(_walk("a.js", "javascript", source))

    if "cb =" in source:
        # A default value sits in the parameters, outside the walked body.
        assert shape == {"outer": (1, 0)}
    else:
        assert shape == {"outer": (4, 3)}


def test_an_expression_bodied_arrow_bound_to_a_name_rolls_up():
    source = "function outer(){ const pick = (x) => (x ? (a ? 1 : 2) : 3); }\n"

    shape = _shape(_walk("a.js", "javascript", source))

    assert list(shape) == ["outer"]
    assert shape["outer"][0] == 3


def test_a_unit_inside_a_unit_belongs_to_the_inner_one():
    source = (
        "function outer(){\n"
        "  const middle = () => {\n"
        "    if (z) { }\n"
        f"    const inner = () => {{ {_C3} }};\n"
        "  };\n"
        "}\n"
    )

    fcx = _walk("a.js", "javascript", source)

    assert _shape(fcx) == {"outer": (1, 0), "middle": (2, 1), "inner": (4, 3)}
    # Four branch points in the file, each counted once.
    assert sum(fc.ccn - 1 for fc in fcx.functions) == 4


def test_a_switch_in_a_unit_is_not_the_enclosing_function_s_dispatch():
    cases = "".join(f"case {i}: return {i};\n" for i in range(4))
    source = (
        "function outer(){\n"
        "  const route = (kind) => {\n"
        f"    switch (kind) {{\n{cases}    }}\n"
        "  };\n"
        "  return route;\n"
        "}\n"
    )

    by_name = _by_name(_walk("a.js", "javascript", source))

    assert by_name["outer"].ccn == 1
    assert by_name["outer"].dispatch_share == 0.0
    assert by_name["route"].dispatch_share > 0.0


class TestTheIssueReproduction:
    """The three shapes of #4575 measure the same once the fix is in."""

    CHECK = (
        "\n"
        "    if (a) { if (b) { if (c) { out.push(1); } } }\n"
        "    if (d) { out.push(2); } else if (e) { out.push(3); }\n"
        "    for (const x of xs) { if (x > 1 && x < 9) { out.push(x); } }\n"
    )

    def _source(self, shape: str) -> str:
        if shape == "arrows":
            head, unit, tail = "(() => {\n", "  const check{i} = () => {{{body}  }};\n", "})();\n"
        elif shape == "functions":
            head, unit, tail = "(function () {\n", "  function check{i}() {{{body}  }}\n", "})();\n"
        else:
            return (
                "const out = [];\n"
                + "".join(f"const check{i} = () => {{{self.CHECK}}};\n" for i in range(3))
                + "check0(); check1(); check2();\n"
            )
        units = "".join(unit.format(i=i, body=self.CHECK) for i in range(3))
        return head + "  const out = [];\n" + units + "  check0(); check1(); check2();\n  return out;\n" + tail

    @pytest.mark.parametrize("shape", ["arrows", "functions", "top-level"])
    def test_each_check_is_a_unit(self, shape):
        by_name = _by_name(_walk("a.js", "javascript", self._source(shape)))

        for i in range(3):
            fc = by_name[f"check{i}"]
            assert (fc.ccn, fc.max_nesting, fc.nloc, fc.bumps) == (9, 3, 5, 2)

    @pytest.mark.parametrize("shape", ["arrows", "functions"])
    def test_the_wrapper_measures_only_its_own_statements(self, shape):
        by_name = _by_name(_walk("a.js", "javascript", self._source(shape)))

        wrapper = next(fc for name, fc in by_name.items() if not name.startswith("check"))
        # ``(() => {`` / ``const out = [];`` / the calls / ``return out;`` / ``})();``
        assert (wrapper.ccn, wrapper.max_nesting, wrapper.nloc, wrapper.bumps) == (1, 0, 5, 0)

    def test_file_nloc_is_unchanged(self):
        source = self._source("arrows")

        fcx = _walk("a.js", "javascript", source)

        assert fcx.file_nloc == sum(1 for line in source.splitlines() if line.strip())


class TestNestedLinesLeaveTheEnclosingNloc:
    def test_a_nested_function_declaration(self):
        source = (
            "function outer() {\n"
            "  const x = 1;\n"
            "  function inner() {\n"
            "    if (x) {\n"
            "      return 1;\n"
            "    }\n"
            "  }\n"
            "  return inner;\n"
            "}\n"
        )

        by_name = _by_name(_walk("a.js", "javascript", source))

        # ``{`` / ``const x`` / ``return inner`` / ``}``; inner's five are its own.
        assert by_name["outer"].nloc == 4
        assert by_name["inner"].nloc == 5

    def test_a_named_arrow_and_its_binding(self):
        source = (
            "function outer() {\n"
            "  const check = () => {\n"
            "    if (a) {\n"
            "      return 1;\n"
            "    }\n"
            "  };\n"
            "  return check;\n"
            "}\n"
        )

        by_name = _by_name(_walk("a.js", "javascript", source))

        assert by_name["outer"].nloc == 3
        assert by_name["check"].nloc == 5

    def test_a_first_row_shared_with_the_enclosing_code_stays_with_it(self):
        source = (
            "function outer() {\n"
            "  setup(); const check = () => {\n"
            "    if (a) { }\n"
            "  };\n"
            "  return check;\n"
            "}\n"
        )

        by_name = _by_name(_walk("a.js", "javascript", source))

        # ``{`` / ``setup(); const check = () => {`` / ``return check`` / ``}``
        assert by_name["outer"].nloc == 4

    def test_a_last_row_shared_with_the_enclosing_code_stays_with_it(self):
        source = (
            "function outer() {\n"
            "  function inner() {\n"
            "    if (a) { }\n"
            "  } inner();\n"
            "}\n"
        )

        by_name = _by_name(_walk("a.js", "javascript", source))

        # ``{`` / ``} inner();`` / ``}``
        assert by_name["outer"].nloc == 3
        assert by_name["inner"].nloc == 3

    def test_a_row_two_units_share_is_taken_away_once(self):
        source = (
            "function outer() {\n"
            "  function one() {\n"
            "    if (a) { }\n"
            "  } function two() {\n"
            "    if (b) { }\n"
            "  }\n"
            "}\n"
        )

        by_name = _by_name(_walk("a.js", "javascript", source))

        # Only ``{`` and ``}`` are outer's own.
        assert by_name["outer"].nloc == 2

    def test_a_unit_with_comments_and_blank_lines(self):
        source = (
            "function outer() {\n"
            "  // set up\n"
            "  const check = () => {\n"
            "\n"
            "    // the check\n"
            "    if (a) { }\n"
            "  };\n"
            "  return check;\n"
            "}\n"
        )

        by_name = _by_name(_walk("a.js", "javascript", source))

        assert by_name["outer"].nloc == 3
        assert by_name["check"].nloc == 3

    def test_a_python_nested_def(self):
        source = (
            "def outer():\n"
            "    x = 1\n"
            "    def inner():\n"
            "        return x\n"
            "    return inner\n"
        )

        by_name = _by_name(_walk("a.py", "python", source))

        assert by_name["outer"].nloc == 2
        assert by_name["inner"].nloc == 1

    def test_a_markup_row_of_a_unit_leaves_the_enclosing_markup_count(self):
        source = (
            "function List() {\n"
            "  const Row = () => {\n"
            "    return (\n"
            "      <li>\n"
            "        <span />\n"
            "      </li>\n"
            "    );\n"
            "  };\n"
            "  return (\n"
            "    <ul>\n"
            "      <Row />\n"
            "    </ul>\n"
            "  );\n"
            "}\n"
        )

        by_name = _by_name(_walk("a.jsx", "javascript", source))

        outer, row = by_name["List"], by_name["Row"]
        assert outer.markup_nloc == 3
        assert row.markup_nloc == 3
        assert outer.nloc - outer.markup_nloc == 4


def test_a_method_s_facts_still_read_the_arrow_inside_it():
    """The arrow is a unit for the complexity metrics, but the facts of the
    method around it read it as the lambda it is, as with a callback."""
    as_unit = (
        "class A {\n"
        "  m() {\n"
        "    const save = () => { this.saved = true; };\n"
        "    return save;\n"
        "  }\n"
        "}\n"
    )
    as_callback = (
        "class A {\n"
        "  m() {\n"
        "    return run(() => { this.saved = true; });\n"
        "  }\n"
        "}\n"
    )

    unit_facts = _by_name(_walk("a.ts", "typescript", as_unit))["m"].facts
    callback_facts = _by_name(_walk("a.ts", "typescript", as_callback))["m"].facts

    assert unit_facts is not None
    assert (unit_facts.uses_receiver, unit_facts.receiver_assigns) == (True, ("saved",))
    assert unit_facts == callback_facts


def test_a_top_level_arrow_in_an_object_is_named_by_its_key():
    source = f"module.exports = {{ check: () => {{ {_C3} }} }};\n"

    assert _shape(_walk("a.js", "javascript", source)) == {"check": (4, 3)}


def test_a_python_lambda_in_a_dict_keeps_its_name():
    """Only JS/TS arrows are read through an object pair."""
    source = "HANDLERS = {'a': lambda x: x}\n"

    names = [fc.name for fc in _walk("a.py", "python", source).functions]

    assert names == ["<anonymous@1>"]
