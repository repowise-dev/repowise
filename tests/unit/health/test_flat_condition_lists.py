"""A one-operator chain that negates whole operands reads as a list, not a knot.

``!a && !b && !c`` (flags) and ``!strcmp(name, ".") || !strcmp(name, "..")``
(one name) stay flat, so complex_conditional does not fire. A negated
sub-chain, mixed operators, or operands about different things still fire.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

_EXT = {"c": "c", "javascript": "js", "python": "py", "ruby": "rb", "dart": "dart"}


def _flat(language: str, source: str) -> list[bool]:
    fcx = walk_file(f"/tmp/sample.{_EXT[language]}", language, source.encode())
    if not fcx.functions:
        pytest.skip(f"{language} grammar unavailable")
    return [c.flat_chain for fn in fcx.functions for c in fn.complex_conditions]


@pytest.mark.parametrize(
    ("language", "source"),
    [
        # redis aof.c: every operand tests ``name``.
        (
            "c",
            'int f(char *name) { if (!strcmp(name, ".") || !strcmp(name, "..") ||'
            " !strcmp(name, manifest) || refs(am, name)) return 1; return 0; }",
        ),
        # redis sentinel.c: "none of" over one name.
        (
            "c",
            'int f(char *v) { if (!(!strcmp(v, "debug") || !strcmp(v, "verbose") ||'
            ' !strcmp(v, "notice"))) return 1; return 0; }',
        ),
        # eshop catalog-sort.js: every operand reads ``event``.
        (
            "javascript",
            "function f(event) { if (event.key.length === 1 && /\\S/.test(event.key) &&"
            " !event.ctrlKey && !event.metaKey && !event.altKey) { go(); } }",
        ),
        # Bare flags, some negated.
        ("python", "def f(a, b, c, d):\n    if not a and not b and c and not d:\n        go()\n"),
        ("ruby", "def f\n  if !@done && !ready && !closed && open\n    go\n  end\nend\n"),
        # dart-pub report.dart: "none of" these flags.
        (
            "dart",
            "void f() { if (!(alwaysShow || changed || addedOrRemoved || typeChanged)) { go(); } }",
        ),
    ],
)
def test_negated_lists_stay_flat(language: str, source: str) -> None:
    assert _flat(language, source) == [True]


@pytest.mark.parametrize(
    ("language", "source"),
    [
        # sdwebimage UIView+WebCache.m shape: different things, one negated.
        (
            "javascript",
            "function f() { if (progress && finished && !error && progress.total == 0"
            " && progress.done == 0) { go(); } }",
        ),
        # A negated sub-chain is De Morgan, not a list.
        ("javascript", "function f(a, b, c) { if (a && b && !(c && d)) { go(); } }"),
        # Only the function is shared; the values tested differ.
        (
            "c",
            'int f(void) { if (!strcmp(a, "x") || !strcmp(b, "y") || !strcmp(c, "z")) return 1;'
            " return 0; }",
        ),
        # Only the receiver is shared.
        (
            "javascript",
            "function f() { if (!this.a(x) && this.b(y) && !this.c(z)) { go(); } }",
        ),
        # Mixed operators.
        ("python", "def f(a, b, c):\n    if not a and (b or not c) and d:\n        go()\n"),
    ],
)
def test_knots_still_fire(language: str, source: str) -> None:
    assert _flat(language, source) == [False]
