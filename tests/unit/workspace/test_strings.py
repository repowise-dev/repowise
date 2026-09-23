"""The shared string-expression layer: argument selection and syntax lookup."""

from __future__ import annotations

import pytest

from repowise.core.workspace.extractors.strings import (
    JS_SYNTAX,
    PHP_SYNTAX,
    PYTHON_SYNTAX,
    Arg,
    resolve_argument,
    select_argument,
    syntax_for_suffix,
)


class TestSelectArgument:
    @pytest.mark.parametrize(
        ("args", "arg", "expected"),
        [
            (["'a'", "'b'"], Arg(pos=1), ["'b'"]),
            (["queue='jobs'", "cb"], Arg(keys=("queue",), pos=0), ["'jobs'"]),
            (["{ topic: 'a', messages }"], Arg(keys=("topic",)), ["'a'"]),
            (['{"topic": "a"}'], Arg(keys=("topic",)), ['"a"']),
            (["['queue' => 'jobs', 'delay' => 5]"], Arg(keys=("queue",)), ["'jobs'"]),
            (['topics = {"a", "b"}'], Arg(keys=("topics",)), ['"a"', '"b"']),
            (["['orders:a', 'orders:b']"], Arg(pos=0), ["'orders:a'", "'orders:b'"]),
            (["'a'"], Arg(pos=3), []),
            (["x == 'a'"], Arg(keys=("x",)), []),
        ],
    )
    def test_selection(self, args: list[str], arg: Arg, expected: list[str]) -> None:
        assert select_argument(args, arg) == expected


class TestResolveArgument:
    def test_a_constant_folds(self) -> None:
        got = resolve_argument(["QUEUE"], Arg(pos=0), PYTHON_SYNTAX, {"QUEUE": "'jobs'"})
        assert got == (["jobs"], False)

    def test_a_template_with_a_hole_is_refused(self) -> None:
        assert resolve_argument(["`jobs.${env}`"], Arg(pos=0), JS_SYNTAX, {}) == ([], True)

    def test_an_absent_argument_is_not_a_refusal(self) -> None:
        assert resolve_argument(["'a'"], Arg(pos=2), JS_SYNTAX, {}) == ([], False)

    def test_php_map_value(self) -> None:
        got = resolve_argument(["['queue' => 'jobs']"], Arg(keys=("queue",)), PHP_SYNTAX, {})
        assert got == (["jobs"], False)


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [(".ts", JS_SYNTAX), (".py", PYTHON_SYNTAX), (".php", PHP_SYNTAX), (".sql", None)],
)
def test_syntax_for_suffix(suffix: str, expected: object) -> None:
    assert syntax_for_suffix(suffix) is expected
