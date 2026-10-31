"""A loop-scoped perf finding needs the statement to run on every pass.

When a later statement in the same block ends the loop (a ``return``, a
``break`` of the only loop, a throw no ``try`` catches) the statement runs at
most once per loop entry, so no loop marker fires on it. A conditional exit
keeps the finding.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity.walker import walk_file

_EXT = {"python": "py", "go": "go", "typescript": "ts", "java": "java"}

_PY = "import pandas as pd\nimport requests\n\ndef f(xs, df, lock, ok):\n    big = [1, 2]\n    for x in xs:\n{body}"
_GO = 'package m\nimport "net/http"\nfunc f(xs []string, ok bool, s string) {{\n\tfor _, x := range xs {{\n{body}\n\t}}\n}}'
_TS = "async function f(xs, ok) {{\n  for (const x of xs) {{\n{body}\n  }}\n}}"
_JAVA = (
    "import java.util.regex.Pattern;\nclass A {{ void f(java.util.List<String> xs, boolean ok, String s) {{\n"
    "  for (String x : xs) {{\n{body}\n  }}\n}} }}"
)

_CASES = [
    # (language, template, body, marker)
    ("python", _PY, "        df = pd.concat([df, x])\n{exit}", "pd_concat_in_loop"),
    ("python", _PY, "        requests.get(x)\n{exit}", "io_in_loop"),
    ("python", _PY, "        lock.acquire()\n{exit}", "lock_in_loop"),
    ("python", _PY, "        y = x in big\n{exit}", "membership_test_against_list_in_loop"),
    ("python", _PY, '        s += "row"\n{exit}', "string_concat_in_loop"),
    ("go", _GO, "\t\thttp.Get(x)\n{exit}", "io_in_loop"),
    ("go", _GO, '\t\ts += "row"\n{exit}', "string_concat_in_loop"),
    ("go", _GO, "\t\tdefer http.Get(x)\n{exit}", "defer_in_loop"),
    ("typescript", _TS, "    const y = JSON.parse(JSON.stringify(x));\n{exit}", "json_parse_in_loop"),
    ("typescript", _TS, "    await fetch(x);\n{exit}", "io_in_loop"),
    ("typescript", _TS, "    await fetch(x);\n{exit}", "serial_await_in_loop"),
    ("java", _JAVA, '    Pattern.compile("a+");\n{exit}', "regex_compile_in_loop"),
    ("java", _JAVA, '    s += "line";\n{exit}', "string_concat_in_loop"),
]

_EXITS = {
    "python": ("        return\n", "        if ok:\n            return\n"),
    "go": ("\t\tbreak", "\t\tif ok {\n\t\t\tbreak\n\t\t}"),
    "typescript": ("    break;", "    if (ok) {\n      break;\n    }"),
    "java": ("    return;", "    if (ok) {\n      return;\n    }"),
}


def _kinds(lang: str, src: str) -> set[str]:
    fc = walk_file(f"t.{_EXT[lang]}", lang, src.encode())
    return {h.kind for h in fc.perf_hits}


def _ids(case):
    return f"{case[0]}-{case[3]}"


@pytest.mark.parametrize("case", _CASES, ids=_ids)
def test_exit_after_statement_suppresses(case):
    lang, template, body, marker = case
    src = template.format(body=body.format(exit=_EXITS[lang][0]))
    assert marker not in _kinds(lang, src)


@pytest.mark.parametrize("case", _CASES, ids=_ids)
def test_conditional_exit_still_fires(case):
    lang, template, body, marker = case
    src = template.format(body=body.format(exit=_EXITS[lang][1]))
    assert marker in _kinds(lang, src)


@pytest.mark.parametrize(
    "lang,src",
    [
        # A ``break`` in a switch case leaves the switch, not the loop.
        (
            "typescript",
            "async function f(xs) { for (const x of xs) { switch (x) { case 1: await fetch(x); break; } } }",
        ),
        # A throw a ``try`` around it can catch lets the loop go on.
        (
            "java",
            "import java.util.regex.Pattern;\nclass A { void f(java.util.List<String> xs) {\n"
            '  for (String x : xs) { try { Pattern.compile("a+"); throw new RuntimeException(); }'
            " catch (RuntimeException e) {} } } }",
        ),
        # A labeled ``break`` is not followed.
        (
            "java",
            "import java.util.regex.Pattern;\nclass A { void f(java.util.List<String> xs) {\n"
            '  for (String x : xs) { lbl: { Pattern.compile("a+"); break lbl; } } } }',
        ),
        # A ``continue`` in the statement itself loops back before the exit.
        (
            "python",
            "import requests\ndef f(xs):\n    for x in xs:\n        if requests.get(x):\n"
            "            continue\n        return\n",
        ),
        (
            "typescript",
            "async function f(xs) { for (const x of xs) { if (await fetch(x)) { continue; } return; } }",
        ),
        (
            "go",
            'package m\nimport "net/http"\nfunc f(xs []string) {\n\tfor _, x := range xs {\n'
            "\t\tif _, e := http.Get(x); e != nil {\n\t\t\tcontinue\n\t\t}\n\t\treturn\n\t}\n}",
        ),
        # A ``case`` label is tested on every dispatch.
        (
            "typescript",
            "async function f(xs) { for (const x of xs) { switch (x) { case await fetch(x): return; } } }",
        ),
        # A ``break`` in an inner loop leaves only the inner loop.
        (
            "python",
            "import requests\ndef f(xs, ys):\n    for x in xs:\n        for y in ys:\n"
            "            requests.get(y)\n            break\n",
        ),
        # An exit after an inner loop does not stop that loop repeating its body or condition.
        (
            "python",
            "import requests\ndef f(xs, ys):\n    for x in xs:\n        for y in ys:\n"
            "            requests.get(y)\n        return\n",
        ),
        (
            "python",
            "import requests\ndef f(xs):\n    for x in xs:\n        while requests.get(x):\n"
            "            pass\n        return\n",
        ),
        (
            "typescript",
            "async function f(xs, ys) { for (const x of xs) { for (const y of ys) await fetch(y); return; } }",
        ),
        # A ``return`` inside a closure run in place ends the closure, not the loop.
        (
            "typescript",
            "async function f(xs) { for (const x of xs) {"
            " await limit(async () => { await fetch(x); return; }); } }",
        ),
    ],
)
def test_exit_that_does_not_end_the_loop_keeps_the_finding(lang, src):
    kinds = _kinds(lang, src)
    assert kinds & {"io_in_loop", "regex_compile_in_loop"}


def test_return_inside_try_still_ends_the_loop():
    """No handler catches a ``return``, so a ``try`` around it keeps the gate."""
    src = (
        "import requests\ndef f(xs):\n    for x in xs:\n        try:\n"
        "            requests.get(x)\n            return\n        except ValueError:\n            pass\n"
    )
    assert "io_in_loop" not in _kinds("python", src)


@pytest.mark.parametrize(
    "exit_src,recorded",
    [("        return\n", False), ("        if ok:\n            return\n", True)],
    ids=["exit", "conditional-exit"],
)
def test_helper_call_before_an_exit_is_no_loop_target(exit_src, recorded):
    src = _PY.format(body="        helper(x)\n" + exit_src)
    fc = walk_file("t.py", "python", src.encode())
    targets = {name for f in fc.perf_fn_facts for name, _line in f.loop_call_targets}
    assert ("helper" in targets) is recorded


@pytest.mark.parametrize(
    "exit_src,recorded",
    [("        return\n", False), ("        if ok:\n            return\n", True)],
    ids=["exit", "conditional-exit"],
)
def test_inner_loop_before_an_exit_records_no_nested_loop(exit_src, recorded):
    src = (
        "def f(xs, ok):\n    for x in xs:\n        for y in xs:\n"
        "            if x == y:\n                print(x)\n" + exit_src
    )
    fc = walk_file("t.py", "python", src.encode())
    assert any(f.nested_loop_line for f in fc.perf_fn_facts) is recorded
