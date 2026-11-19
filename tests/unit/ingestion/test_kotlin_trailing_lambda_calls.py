"""A Kotlin call whose only argument is a trailing lambda is a call site (#3762).

``AppTheme { ... }`` has no parentheses, so the grammar gives it no
``value_arguments`` and the call patterns used to skip it. It now reaches its
callee with one argument, like ``AppTheme({ ... })``, and the stdlib and
function-typed refusals still hold.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file

SOURCE = """\
package app

fun AppTheme(content: () -> Unit) = content()

fun h2(a: Int, b: Int) {}

class Segments {
    fun forEachSegment(action: (Int) -> Unit) {}
}

class Runner {
    fun run(block: () -> Unit) {}

    fun scoped() {
        run { println("x") }
    }
}

fun withParentheses() {
    AppTheme({ println("x") })
}

fun lambdaOnly() {
    AppTheme { println("x") }
}

fun memberWithParentheses(segments: Segments) {
    segments.forEachSegment({ })
}

fun memberLambdaOnly(segments: Segments) {
    segments.forEachSegment { }
}

fun wrongArity() {
    h2 { }
}

fun callback(onClick: (() -> Unit) -> Unit) {
    onClick { }
}

fun counts(a: Int) {
    foo { }
    foo(a) { }
    foo(a)
}
"""


@pytest.fixture
def parsed(tmp_path: Path):
    path = tmp_path / "Use.kt"
    path.write_text(SOURCE, encoding="utf-8")
    info = FileInfo(
        path="Use.kt",
        abs_path=str(path),
        language="kotlin",
        size_bytes=len(SOURCE),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return {"Use.kt": parse_file(info, SOURCE.encode("utf-8"))}


def _callees(parsed, tmp_path: Path, caller: str) -> set[str]:
    resolver = CallResolver(parsed, {"Use.kt": set()}, repo_path=str(tmp_path))
    return {
        rc.callee_id
        for rc in resolver.resolve_file("Use.kt", parsed["Use.kt"].calls)
        if rc.caller_id == f"Use.kt::{caller.replace('.', '::')}"
    }


@pytest.mark.parametrize("caller", ["withParentheses", "lambdaOnly"])
def test_a_lambda_only_call_reaches_its_function(parsed, tmp_path: Path, caller: str) -> None:
    assert _callees(parsed, tmp_path, caller) == {"Use.kt::AppTheme"}


@pytest.mark.parametrize("caller", ["memberWithParentheses", "memberLambdaOnly"])
def test_a_lambda_only_member_call_reaches_its_method(parsed, tmp_path: Path, caller: str) -> None:
    assert _callees(parsed, tmp_path, caller) == {"Use.kt::Segments::forEachSegment"}


def test_a_stdlib_scope_function_stays_unresolved(parsed, tmp_path: Path) -> None:
    assert _callees(parsed, tmp_path, "Runner.scoped") == set()


def test_a_function_that_cannot_take_one_argument_is_not_called(parsed, tmp_path: Path) -> None:
    assert "Use.kt::h2" not in _callees(parsed, tmp_path, "wrongArity")


def test_a_function_typed_value_has_no_repo_callee(parsed, tmp_path: Path) -> None:
    assert _callees(parsed, tmp_path, "callback") == set()


def test_the_trailing_lambda_counts_as_an_argument(parsed) -> None:
    counts = sorted(
        (c.line, c.argument_count) for c in parsed["Use.kt"].calls if c.target_name == "foo"
    )
    lines = SOURCE.splitlines()
    by_text = {lines[line - 1].strip(): count for line, count in counts}
    assert by_text == {"foo { }": 1, "foo(a) { }": 2, "foo(a)": 1}
