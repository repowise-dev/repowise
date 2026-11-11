"""A ternary's else-arm dispatch stays flat; decisions inside an arm still nest."""

from __future__ import annotations

import re

import pytest

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.nested_complexity import BIOMARKER
from repowise.core.analysis.health.complexity import walk_file
from repowise.core.ingestion.parser import _get_language

_LANGUAGES = [
    ("java", "F.java", "class F { int f(boolean a, boolean b, boolean c) {", "} }"),
    ("c", "f.c", "int f(int a, int b, int c) {", "}"),
    ("cpp", "f.cpp", "int f(bool a, bool b, bool c) {", "}"),
    ("csharp", "F.cs", "class F { int f(bool a, bool b, bool c) {", "} }"),
    ("dart", "f.dart", "int f(bool a, bool b, bool c) {", "}"),
    ("objectivec", "f.m", "int f(int a, int b, int c) {", "}"),
    ("php", "f.php", "<?php function f($a, $b, $c) {", "}"),
]


@pytest.mark.parametrize("language,path,prefix,suffix", _LANGUAGES, ids=[v[0] for v in _LANGUAGES])
@pytest.mark.parametrize(
    "body,expected",
    [
        pytest.param("return a ? 1 : (b ? 2 : (c ? 3 : 4));", (4, 1, 3), id="dispatch"),
        pytest.param("return a ? (b ? 1 : 2) : 3;", (3, 2, 3), id="nested-consequence"),
        pytest.param("return (a ? b : c) ? 1 : 2;", (3, 2, 3), id="nested-condition"),
        pytest.param(
            "if (a) { return b ? 1 : (c ? 2 : 3); } return 0;",
            (4, 2, 4),
            id="dispatch-inside-if",
        ),
        pytest.param(
            "if (a ? b : (c ? a : b)) { return 1; } return 0;",
            (4, 2, 4),
            id="dispatch-in-if-condition",
        ),
    ],
)
def test_ternary_dispatch_and_real_nesting(language, path, prefix, suffix, body, expected):
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if language == "php":
        body = re.sub(r"\b([abc])\b", r"$\1", body)
    [fn] = walk_file(path, language, f"{prefix}\n{body}\n{suffix}".encode()).functions
    assert (fn.ccn, fn.max_nesting, fn.cognitive) == expected


@pytest.mark.parametrize("language,path,prefix,suffix", _LANGUAGES, ids=[v[0] for v in _LANGUAGES])
def test_dispatch_does_not_report_a_deeply_nested_function(language, path, prefix, suffix):
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    body = "return a ? 1 : (b ? 2 : (c ? 3 : (a ? 4 : (b ? 5 : 6))));"
    if language == "php":
        body = re.sub(r"\b([abc])\b", r"$\1", body)
    [fn] = walk_file(path, language, f"{prefix}\n{body}\n{suffix}".encode()).functions
    assert fn.ccn == 6
    context = FileContext(
        file_path=path, language=language, nloc=3, has_test_file=False, module=None,
        all_functions=(fn,),
    )
    assert BIOMARKER.detect(context) == []


@pytest.mark.parametrize(
    "language,path,prefix,suffix", [v for v in _LANGUAGES if v[0] != "php"],
    ids=[v[0] for v in _LANGUAGES if v[0] != "php"],
)
def test_unparenthesized_dispatch(language, path, prefix, suffix):
    # PHP 8 requires the explicit grouping covered above; these grammars are
    # right-associative without it.
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    body = "return a ? 1 : b ? 2 : c ? 3 : 4;"
    [fn] = walk_file(path, language, f"{prefix}\n{body}\n{suffix}".encode()).functions
    assert (fn.ccn, fn.max_nesting, fn.cognitive) == (4, 1, 3)


_LAMBDAS = {
    "java": "IntSupplier pick = () -> %s; return pick.getAsInt();",
    "cpp": "auto pick = [&]() { return %s; }; return pick();",
    "csharp": "System.Func<int> pick = () => %s; return pick();",
    "dart": "final pick = () { return %s; }; return pick();",
    "objectivec": "int (^pick)(void) = ^{ return %s; }; return pick();",
    "php": "$pick = function () use ($a, $b, $c) { return %s; }; return $pick();",
}


@pytest.mark.parametrize(
    "language,path,prefix,suffix", [v for v in _LANGUAGES if v[0] in _LAMBDAS],
    ids=[v[0] for v in _LANGUAGES if v[0] in _LAMBDAS],
)
@pytest.mark.parametrize(
    "expression,expected",
    [
        pytest.param("a ? 1 : (b ? 2 : 3)", (3, 1, 2), id="lambda-dispatch"),
        pytest.param("a ? (b ? 1 : 2) : 3", (3, 2, 3), id="lambda-nested-consequence"),
    ],
)
def test_lambda_keeps_dispatch_distinct_from_nesting(language, path, prefix, suffix, expression, expected):
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if language == "php":
        expression = re.sub(r"\b([abc])\b", r"$\1", expression)
    body = _LAMBDAS[language] % expression
    [fn] = walk_file(path, language, f"{prefix}\n{body}\n{suffix}".encode()).functions
    assert (fn.ccn, fn.max_nesting, fn.cognitive) == expected
