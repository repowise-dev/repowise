"""Equivalence tests for the delimiter-windowed _FUNC_DEF_RE scan.

``_iter_func_defs`` must return exactly what a full-text
``_FUNC_DEF_RE.finditer`` returns — same spans, same captured names, in
the same order — on every C++ shape we can think of, including the
catastrophic-backtracking inputs that motivated the windowing and the
``noexcept`` argument hole that forces the full-text fallback.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.dynamic_hints.cpp import (
    _FUNC_DEF_RE,
    _NOEXCEPT_HOLE_RE,
    _iter_func_defs,
)


def _reference(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(1)) for m in _FUNC_DEF_RE.finditer(text)]


def _windowed(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(1)) for m in _iter_func_defs(text)]


CORPUS = {
    "plain_function": "int add(int a, int b) {\n    return a + b;\n}\n",
    "out_of_class_method": (
        "ReturnType Class::method(int x) const noexcept {\n    return {};\n}\n"
    ),
    "multiline_prefix_type": (
        "std::map<std::string,\n         std::vector<int>>\nlookup_table(const Key& k) {\n"
        "    return {};\n}\n"
    ),
    "trailing_return": "auto make_widget(int id) -> std::unique_ptr<Widget> {\n    return nullptr;\n}\n",
    "keyword_loose_match": "void f() {\n    if (cond) {\n        run();\n    }\n}\n",
    "match_at_position_zero": "int main() {\n    return 0;\n}\n",
    "no_functions_at_all": "// just a comment\nconstexpr int kAnswer = 42;\n",
    "empty_text": "",
    "windows_without_parens": "namespace foo {\nstruct Bar {\n    int x;\n};\n}\n",
    "paren_but_no_match": "int x = (1 + 2);\nint y = 3;\n",
    "initializer_run_small": (
        "static inline const std::vector<LetterKey> letters = { LetterKey::VK_0,\n"
        + "\n".join(
            f"    LetterKey::VK_{c}," for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        )
        + "\n    LetterKey::VK_BACKSLASH, };\n"
        "void after_the_array() {\n    run();\n}\n"
    ),
    "brace_init_members": (
        "struct Settings {\n"
        "    Key activationKey{ Key::Both };\n"
        "    bool gameMode{ true };\n"
        "    std::chrono::milliseconds inputTime{ 300 };\n"
        "};\n"
        "void tick() {\n}\n"
    ),
    "same_line_after_semicolon": "foo(); int bar() {\n}\n",
    "two_defs_same_line": "void a() { x(); } void b() { y(); }\n",
    "noexcept_plain": "bool on_key(Info info) noexcept {\n    return false;\n}\n",
    "noexcept_with_args": (
        "bool on_key(Info info) noexcept(std::is_nothrow_constructible_v<Info>) {\n"
        "    return false;\n}\n"
    ),
    "noexcept_semicolon_after_paren": (
        # The ';' sits after the first ')' inside the noexcept args, so the
        # pattern's own [^)]* cannot cross it either — no fallback needed,
        # behaviour must still be identical.
        "bool weird(Info info) noexcept(call(1); 2) {\n    return false;\n}\n"
    ),
    "noexcept_hole_semicolon": (
        # ';' BEFORE the first ')' after 'noexcept(' — the only shape where
        # a match could span a delimiter; must trigger the fallback.
        "bool weird(Info info) noexcept(a; b) {\n    return false;\n}\n"
    ),
    "noexcept_hole_brace": (
        "bool weird(Info info) noexcept(T{}) {\n    return false;\n}\n"
        "void normal_after() {\n    run();\n}\n"
    ),
    "consecutive_braces": "void f() {{\n    nested();\n}}\n",
    "unbalanced_open_paren": "void broken(int x {\n    run();\n}\n",
    "args_span_lines": (
        "LRESULT CALLBACK LowLevelKeyboardProc(int nCode,\n"
        "                                      WPARAM wParam,\n"
        "                                      LPARAM lParam) {\n"
        "    return 0;\n}\n"
    ),
    "operator_like_assignment": "auto v = transform(input);\nvoid g() {\n    h();\n}\n",
    "class_with_methods": (
        "class Widget {\n"
        "public:\n"
        "    void render(Canvas& c) override {\n        c.draw();\n    }\n"
        "    int size() const {\n        return n_;\n    }\n"
        "private:\n"
        "    int n_;\n"
        "};\n"
    ),
}


@pytest.mark.parametrize("label", sorted(CORPUS))
def test_windowed_matches_reference(label: str) -> None:
    text = CORPUS[label]
    assert _windowed(text) == _reference(text)


def test_noexcept_hole_takes_fallback_path() -> None:
    text = CORPUS["noexcept_hole_semicolon"]
    assert _NOEXCEPT_HOLE_RE.search(text) is not None
    assert _windowed(text) == _reference(text)


def test_concatenated_corpus_matches_reference() -> None:
    # One big file mixing every shape; exercises window bookkeeping
    # across many delimiters and the pos advancement after each match.
    text = "\n".join(CORPUS[k] for k in sorted(CORPUS))
    assert _windowed(text) == _reference(text)


def test_pathological_run_is_fast_and_equivalent() -> None:
    # The KeyboardListener.h shape: a multi-KB run of prefix-class chars
    # (identifiers, ::, commas, whitespace) with no parens. The full-text
    # scan blows up combinatorially; windowed must stay instant and equal.
    run = "static inline const std::vector<LetterKey> letters = {\n" + "".join(
        f"    LetterKey::VK_{i},\n" for i in range(250)
    ) + "};\n"
    text = run + "void after(int x) {\n    run();\n}\n"
    import time

    t0 = time.monotonic()
    got = _windowed(text)
    elapsed = time.monotonic() - t0
    assert got == _reference(text)
    assert elapsed < 1.0
