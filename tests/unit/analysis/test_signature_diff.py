"""Tests for semantic signature difference classification."""

from __future__ import annotations

from repowise.core.analysis.signature_diff import (
    EFFECT_BREAKING,
    EFFECT_COMPATIBLE,
    EFFECT_NONE,
    EFFECT_UNKNOWN,
    classify_signature_change,
    parse_parameters,
)


def test_parse_parameters_python():
    params = parse_parameters("def foo(x: int, y: str = 'default', *args, **kwargs) -> bool")
    assert params is not None
    assert len(params) == 4
    assert params[0].name == "x"
    assert params[0].type_annotation == "int"
    assert params[0].default is None
    assert not params[0].is_optional

    assert params[1].name == "y"
    assert params[1].type_annotation == "str"
    assert params[1].default == "'default'"
    assert params[1].is_optional

    assert params[2].name == "args"
    assert params[2].is_vararg
    assert params[2].is_optional

    assert params[3].name == "kwargs"
    assert params[3].is_kwarg
    assert params[3].is_optional


def test_parse_parameters_typescript():
    params = parse_parameters("function bar(a: number, b?: string, c: boolean = true): void")
    assert params is not None
    assert len(params) == 3
    assert params[0].name == "a"
    assert not params[0].is_optional

    assert params[1].name == "b"
    assert params[1].is_optional

    assert params[2].name == "c"
    assert params[2].is_optional


def test_classify_whitespace_and_formatting_is_none():
    base = "def foo(a: int, b: str) -> None"
    head = """def foo(
        a: int,
        b: str,
    ) -> None"""
    effect, reason = classify_signature_change(base, head)
    assert effect == EFFECT_NONE
    assert reason is None


def test_classify_receiver_shift_is_none():
    base = "def get_item(self, index: int) -> str"
    head = "def get_item(cls, index: int) -> str"
    effect, reason = classify_signature_change(base, head)
    assert effect == EFFECT_NONE
    assert reason is None


def test_classify_appended_optional_parameter_is_compatible():
    base = "def fetch(url: str)"
    head = "def fetch(url: str, timeout: int = 30)"
    effect, reason = classify_signature_change(base, head)
    assert effect == EFFECT_COMPATIBLE
    assert reason == "added optional parameter 'timeout'"


def test_classify_multiple_appended_optional_parameters_is_compatible():
    base = "def fetch(url: str)"
    head = "def fetch(url: str, timeout: int = 30, retries: int = 3)"
    effect, reason = classify_signature_change(base, head)
    assert effect == EFFECT_COMPATIBLE
    assert reason == "added optional parameters 'timeout', 'retries'"


def test_classify_removed_parameter_is_breaking():
    base = "def calculate(a: int, b: int, mode: str = 'sum')"
    head = "def calculate(a: int, b: int)"
    effect, reason = classify_signature_change(base, head)
    assert effect == EFFECT_BREAKING
    assert reason == "removed parameter 'mode'"


def test_classify_added_required_parameter_is_breaking():
    base = "def connect(host: str)"
    head = "def connect(host: str, port: int)"
    effect, reason = classify_signature_change(base, head)
    assert effect == EFFECT_BREAKING
    assert reason == "added required parameter 'port'"


def test_classify_default_removed_is_breaking():
    base = "def process(data: str, flag: bool = False)"
    head = "def process(data: str, flag: bool)"
    effect, reason = classify_signature_change(base, head)
    assert effect == EFFECT_BREAKING
    assert reason == "parameter 'flag' is now required"


def test_classify_reordered_parameters_is_breaking():
    base = "def create(name: str, count: int)"
    head = "def create(count: int, name: str)"
    effect, reason = classify_signature_change(base, head)
    assert effect == EFFECT_BREAKING
    assert reason == "reordered parameters"


def test_classify_unparseable_degrades_to_unknown():
    base = "INVALID SIGNATURE STRING ("
    head = "OTHER INVALID )"
    effect, reason = classify_signature_change(base, head)
    assert effect == EFFECT_UNKNOWN
