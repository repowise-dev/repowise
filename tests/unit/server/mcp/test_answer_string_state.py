"""Characterization of the string/comment scanner behind withheld-symbol masking.

``_walk_string_state`` is exercised end to end by the masking and containment
suites, but always through ``withheld_definitions`` or the cached
``_string_masked_lines``. These pin its raw three-part result directly, one
lexical construct per case, so a restructuring of the scanner has to reproduce
the exact line sets rather than just the symbols a fixture happens to surface.
"""

import pytest

from repowise.server.mcp_server.tool_answer.symbols import _walk_string_state


def _walk(src: str, *, backticks: bool = True):
    strings, comments, left_open = _walk_string_state(
        tuple(src.split("\n")), backticks=backticks
    )
    return sorted(strings), sorted(comments), left_open


@pytest.mark.parametrize(
    ("src", "backticks", "expected"),
    [
        # Python triple quotes, both spellings; the opening line is code.
        ('def f():\n    """Doc.\n    def fake():\n    """\n    return 1', True, ([3, 4], [], False)),
        ("x = '''\ndef fake():\n'''\ny = 1", True, ([2, 3], [], False)),
        # Opened and closed on one line: nothing starts inside it.
        ('x = """one line"""\ndef real():', True, ([], [], False)),
        # A triple quote inside an ordinary string or a comment opens nothing.
        ("x = '\"\"\"'\ndef real():", True, ([], [], False)),
        ('x = "a\\"\\"\\"b"\ndef real():', True, ([], [], False)),
        ('# a """ in a comment\ndef real():', True, ([], [], False)),
        ('// a /* in a comment\nfunc real() {', True, ([], [], False)),
        # C-style block comment, closed mid-line with code after it.
        ("/* one\n * two\n */ int x = 1;\nint y;", False, ([], [2, 3], False)),
        # Code after a closing triple quote is scanned again.
        ('a = """\nb\n""" + "/*"\nc = 1', True, ([2, 3], [], False)),
        # A template literal is only a string where backticks are strings.
        ("const q = `\nfunction fake() {\n`;\nfunction real() {", True, ([2, 3], [], False)),
        ("const q = `\nfunction fake() {\n`;\nfunction real() {", False, ([], [], False)),
        # An interpolation holds code: its own lines are not masked, and a
        # backtick inside it nests rather than closing the outer literal.
        ("const a = `x ${\n  f(`inner\n  `)\n} y\n`;\nz", True, ([3, 5], [], False)),
        # Braces inside an interpolation are counted, not taken as its end.
        ("const a = `${ {k: 1}.k }\nstill`;\nz", True, ([2], [], False)),
        # A quoted string or a line comment inside an interpolation.
        ('const a = `${ "`" }\nstill`;\nz', True, ([2], [], False)),
        ("const a = `${ x // `\n}\nrest`;\nz", True, ([3], [], False)),
        # An escaped backtick does not close the literal.
        ("const a = `x\\`y\nstill`;\nz", True, ([2], [], False)),
        # A backtick inside a regex literal opens nothing, after punctuation
        # or after a keyword a regex can follow.
        ("s.replace(/[`]/g, '')\nfunction real() {", True, ([], [], False)),
        ("return /`/.test(x)\nfunction real() {", True, ([], [], False)),
        ("const a = `${s.replace(/[`]/g, '')}\nstill`;\nz", True, ([2], [], False)),
        # A division is not a regex, so the backtick after it does open one.
        ("n = a / b; q = `\nbody\n`", True, ([2, 3], [], False)),
        # Runs left open at EOF: triple quotes and block comments are
        # discarded from the line that opened them; a template is reported.
        ('x = 1\ny = """\nz\nw', True, ([], [], False)),
        ("x = 1\n/* never\nclosed", True, ([], [], False)),
        ('a = """\nb\n"""\nc = """\nd', True, ([2, 3], [], False)),
        ("const a = `\nnever closed", True, ([2], [], True)),
        ("const a = `${\nf(", True, ([], [], True)),
        # A line with nothing quote-like is skipped without a walk.
        ("plain = 1\nother = 2", True, ([], [], False)),
    ],
)
def test_walk_string_state(src, backticks, expected) -> None:
    assert _walk(src, backticks=backticks) == expected
