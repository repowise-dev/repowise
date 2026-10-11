"""What a signature change does to a caller, decided from the two indexed signatures.

Signatures are written the way the indexer emits them (``def f(...) -> T``,
``function f(...) -> T``, ``fn f(...)``, ``func (r *T) M(...)``, ``f(int a) -> T``).
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.signature_effect import (
    EFFECT_BREAKING,
    EFFECT_COMPATIBLE,
    EFFECT_NONE,
    EFFECT_UNKNOWN,
    classify_signature_change,
)

BREAKS = {EFFECT_BREAKING, EFFECT_UNKNOWN}


def _effect(before: str, after: str, lang: str | None, kind: str | None = "function"):
    return classify_signature_change(before, after, lang, kind)


@pytest.mark.parametrize(
    ("before", "after", "lang"),
    [
        ("def run(self, x, y) -> int", "def run(\n    self,\n    x,\n    y\n) -> int", "python"),
        ("def run(x, y)", "def run(x, y,)", "python"),
        # Comments, including an inline one with an apostrophe.
        ("def f(a, b=1)", "def f(\n  a,  # the drawer's row\n  b=1,\n)", "python"),
        ("def f(\n  a,  # it's\n  b=1,\n)", "def f(\n  a,\n  # b's default\n  b=1,\n)", "python"),
        # Rust lifetimes are not string quotes.
        ("fn f(x: &'a str) -> &'a str", "fn f(\n    x: &'a str,\n) -> &'a str", "rust"),
        # Go's grouped parameters are the same parameters.
        ("function F(a, b int)", "function F(a int, b int)", "go"),
        # Reordering keyword-only parameters moves nothing a caller passes by position.
        ("def f(a, *, b=1, c=2)", "def f(a, *, c=2, b=1)", "python"),
    ],
)
def test_text_only_changes_have_no_effect(before, after, lang):
    assert _effect(before, after, lang).effect == EFFECT_NONE


@pytest.mark.parametrize(
    ("before", "after", "lang", "reason"),
    [
        ("def run(x)", "def run(x, y=None)", "python", "added optional `y`"),
        (
            "function run(a: string)",
            "function run(a: string, b?: number)",
            "typescript",
            "added optional `b`",
        ),
        ("def run(x, y)", "def run(x, y=1)", "python", "`y` is now optional"),
        ("def run(x)", "def run(x: int)", "python", "annotated `x`"),
        ("def run(x) -> int", "def run(x) -> str", "python", "changed its return annotation"),
        ("def f(a, t=30)", "def f(a, t='30s')", "python", "changed the default of `t`"),
        ("def f(mode='async')", "def f(mode='sync')", "python", "changed the default of `mode`"),
        (
            "def f(a, b, /)",
            "def f(a, b)",
            "python",
            "`a` can now be passed by name and `b` can now be passed by name",
        ),
        # Callers of these languages never name an argument.
        (
            "function f(a)",
            "function f(b)",
            "javascript",
            "renamed `a` to `b`, which callers never name",
        ),
        (
            "function F(a int)",
            "function F(b int)",
            "go",
            "renamed `a` to `b`, which callers never name",
        ),
        (
            "f(int a) -> void",
            "f(int b) -> void",
            "java",
            "renamed `a` to `b`, which callers never name",
        ),
        ("function F(a int)", "function F(a int, opts ...Opt)", "go", "added `...opts`"),
        ("f(String[] a) -> void", "f(String... a) -> void", "java", "now takes `a` as varargs"),
        (
            "function C({a, b}: {a: string; b: number})",
            "function C({a, b}: {a: string; b?: number})",
            "typescript",
            "member `b` is now optional",
        ),
        (
            "function C({a}: {a: string})",
            "function C({a}: {a: string; b?: number})",
            "typescript",
            "added optional member `b`",
        ),
        ("func (t *T) M(a int)", "func (t T) M(a int)", "go", "moved to a value receiver"),
    ],
)
def test_changes_every_existing_call_survives_are_compatible(before, after, lang, reason):
    effect = _effect(before, after, lang)
    assert effect.effect == EFFECT_COMPATIBLE
    assert effect.reason == reason


@pytest.mark.parametrize(
    ("before", "after", "lang", "reason"),
    [
        ("def run(x, y)", "def run(x)", "python", "removed the required `y`"),
        ("def run(x)", "def run(x, y)", "python", "added the required `y`"),
        ("def run(x, y=1)", "def run(x, y)", "python", "`y` is now required"),
        ("def run(x, y)", "def run(y, x)", "python", "reordered its parameters"),
        (
            "def f(a, timeout=1)",
            "def f(a, deadline=1)",
            "python",
            "renamed `timeout` to `deadline`",
        ),
        ("F(int count) -> void", "F(int n) -> void", "csharp", "renamed `count` to `n`"),
        (
            "def run(x)",
            "async def run(x)",
            "python",
            "became async, so its callers' await changes",
        ),
        # An optional parameter inserted ahead of one callers pass by position.
        ("def f(a, b=1)", "def f(a, c=None, b=1)", "python", "inserted `c` before `b`"),
        (
            "function f(a: string, b?: number)",
            "function f(a: string, c?: string, b?: number)",
            "typescript",
            "inserted `c` before `b`",
        ),
        ("def f(a, *args)", "def f(a, b=None, *args)", "python", "inserted `b` before `*args`"),
        # Variadics are told apart by kind.
        ("def f(a, *args, **kw)", "def f(a, **kw)", "python", "removed `*args`"),
        ("def f(a, *args, **kw)", "def f(a, *args)", "python", "removed `**kw`"),
        ("def f(a, *args)", "def f(a, **kw)", "python", "removed `*args`"),
        ("def f(a, b=1)", "def f(a, *args, b=1)", "python", "made `b` keyword-only"),
        ("def f(a, b=1)", "def f(a, *, b=1)", "python", "made `b` keyword-only"),
        (
            "def f(a, b)",
            "def f(a, b, /)",
            "python",
            "made `a` positional-only and made `b` positional-only",
        ),
        # Shifts in a default value are operators, not generics.
        ("def f(a=1 << 4, b=2, c=8 >> 1)", "def f(a=1 << 4, c=8 >> 1)", "python", "removed `b`"),
        ("func (t T) M(a int)", "func (t *T) M(a int)", "go", "moved to a pointer receiver"),
        # Typed languages: a retype or a new return type breaks the call.
        (
            "function f(x: string)",
            "function f(x: number)",
            "typescript",
            "changed the type of `x` from `string` to `number`",
        ),
        (
            "function f() -> Promise<void>",
            "function f() -> void",
            "typescript",
            "changed its return type from `Promise<void>` to `void`",
        ),
        (
            "fn f(x: &str)",
            "fn f(x: &mut str)",
            "rust",
            "changed the type of `x` from `&str` to `&mut str`",
        ),
        (
            "fn f(x: u32) -> u32",
            "fn f(x: u32) -> Result<u32, E>",
            "rust",
            "changed its return type from `u32` to `Result<u32, E>`",
        ),
        (
            "function F(a int) -> int",
            "function F(a int) -> (int, error)",
            "go",
            "changed its return type from `int` to `(int, error)`",
        ),
        (
            "func (s *S) F(a int) -> int",
            "func (s *S) F(a int) -> (int, error)",
            "go",
            "changed its return type from `int` to `(int, error)`",
        ),
        (
            "foo(int a) -> int",
            "foo(int a) -> String",
            "java",
            "changed its return type from `int` to `String`",
        ),
        (
            "Foo(int a) -> int",
            "Foo(int a) -> string",
            "csharp",
            "changed its return type from `int` to `string`",
        ),
        (
            "function C({a}: {a: string})",
            "function C({a, b}: {a: string; b: number})",
            "typescript",
            "added the required member `b`",
        ),
        (
            "operator()(int a) -> int",
            "operator()(int a, int b) -> int",
            "cpp",
            "added the required `b`",
        ),
    ],
)
def test_changes_that_can_break_a_call_are_breaking(before, after, lang, reason):
    effect = _effect(before, after, lang)
    assert effect.effect == EFFECT_BREAKING
    assert effect.reason == reason


def test_a_static_method_gaining_self_breaks_calls_through_the_class():
    effect = _effect("def f(a)", "def f(self, a)", "python", kind="method")
    assert effect.effect == EFFECT_BREAKING
    assert effect.reason == "became an instance method, so calls through the class break"


def test_a_classmethod_becoming_static_is_compatible():
    effect = _effect("def f(cls, a)", "def f(a)", "python", kind="method")
    assert effect.effect == EFFECT_COMPATIBLE


def test_a_leading_self_is_a_parameter_of_a_module_function():
    effect = _effect("def f(self, a)", "def f(a)", "python", kind="function")
    assert effect.effect == EFFECT_BREAKING


def test_a_named_type_swapped_for_another_is_unknown_not_safe():
    effect = _effect("function C({a}: OldProps)", "function C({a}: NewProps)", "typescript")
    assert effect.effect == EFFECT_UNKNOWN


def test_an_unbalanced_parameter_list_is_unknown_not_safe():
    effect = _effect("def run(x, y)", "def run(x, y: Dict[str, int)", "python")
    assert effect.effect == EFFECT_UNKNOWN


def test_without_a_language_types_and_names_both_count():
    assert _effect("f(x: A)", "f(x: B[])", None).effect in BREAKS
    assert _effect("f(a)", "f(b)", None).effect in BREAKS


def test_async_is_read_from_the_signature_prefix_only():
    effect = _effect("def f(mode='async')", "def f(mode='async', x=1)", "python")
    assert effect.effect == EFFECT_COMPATIBLE


def test_an_arrow_type_does_not_unbalance_the_list():
    before = "function run(cb: (a: number) => void)"
    after = "function run(cb: (a: number) => void, opts?: Options)"
    assert _effect(before, after, "typescript").effect == EFFECT_COMPATIBLE


def test_a_lambda_default_does_not_split_the_list():
    before = "def f(a, key=lambda x, y: x)"
    after = "def f(a, key=lambda x, y: x, b=2)"
    assert _effect(before, after, "python").reason == "added optional `b`"


def test_a_block_comment_inside_the_signature_does_not_make_it_unparseable():
    before = "function Drawer({\n  row,\n}: {\n  /** The drawer's row. */\n  row: Row;\n})"
    after = (
        "function Drawer({\n  row,\n  onClose,\n}: {\n  /** The drawer's row. */\n"
        "  row: Row;\n  onClose?: () => void;\n})"
    )
    assert _effect(before, after, "typescript").effect == EFFECT_COMPATIBLE


def test_a_widened_parameter_or_narrowed_return_type_is_compatible():
    widened = _effect("function f(x: string)", "function f(x: string | null)", "typescript")
    assert widened.effect == EFFECT_COMPATIBLE
    narrowed = _effect("function f() -> string | null", "function f() -> string", "typescript")
    assert narrowed.effect == EFFECT_COMPATIBLE
    # The reverse promises callers something new to handle.
    widened_return = _effect(
        "function f() -> string", "function f() -> string | null", "typescript"
    )
    assert widened_return.effect == EFFECT_BREAKING
