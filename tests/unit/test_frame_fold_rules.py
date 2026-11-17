"""Frames without a symbol of their own: folded, attributed to the file, or skipped."""

from __future__ import annotations

from repowise.core.frame_resolver import ProfileFrame, resolve_frames
from repowise.core.workspace.repo_index import IndexedSymbol

ROOT = "/work/repo"


def _sym(path: str, qualified: str, start: int, end: int, kind: str = "function") -> IndexedSymbol:
    name = qualified.rsplit(".", 1)[-1]
    return IndexedSymbol(
        f"{path}::{name}", name, qualified, kind, "", path, start, end, "public"
    )


def _run(frame: ProfileFrame, symbols: list[IndexedSymbol], lines: list[str] | None = None):
    path = frame.path.removeprefix(ROOT + "/")
    return resolve_frames(
        [frame],
        root=ROOT,
        symbols_by_file={path: symbols},
        read_lines=(lambda _p: lines) if lines is not None else None,
    )


def _hit(res):
    assert len(res.resolved) == 1, res.reasons
    hit = res.resolved[0]
    return hit.symbol, hit.folded


# -- Python ----------------------------------------------------------------------

PY = [
    _sym("app.py", "app.App", 1, 30, "class"),
    _sym("app.py", "app.App.run", 5, 20, "method"),
    _sym("app.py", "app.route", 40, 45),
]
PY_SOURCE = [""] * 37 + ["@cached", "@register(", "    name='x')", "def route():"] + [""] * 6


def _py(line: int, name: str) -> ProfileFrame:
    return ProfileFrame(f"{ROOT}/app.py", line, "python", function=name)


def test_python_nested_def_folds_into_its_parent() -> None:
    assert _hit(_run(_py(8, "App.run.<locals>.inner"), PY)) == (PY[1], True)


def test_python_lambda_and_genexpr_inside_a_method_fold() -> None:
    assert _hit(_run(_py(8, "App.run.<locals>.<lambda>"), PY)) == (PY[1], True)
    assert _hit(_run(_py(8, "App.run.<locals>.<genexpr>"), PY)) == (PY[1], True)


def test_python_doubly_nested_folds_to_the_outermost_indexed_scope() -> None:
    assert _hit(_run(_py(9, "App.run.<locals>.a.<locals>.<lambda>"), PY)) == (PY[1], True)


def test_python_class_level_lambda_folds_into_the_class() -> None:
    assert _hit(_run(_py(25, "App.<lambda>"), PY)) == (PY[0], True)


def test_python_module_level_lambda_folds_into_the_file() -> None:
    assert _hit(_run(_py(35, "<lambda>"), PY)) == (None, True)


def test_python_indexed_nested_def_resolves_directly() -> None:
    nested = _sym("app.py", "app.App.run.inner", 7, 9)
    assert _hit(_run(_py(8, "App.run.<locals>.inner"), [*PY, nested])) == (nested, False)


def test_python_module_body_attributes_to_the_file() -> None:
    assert _hit(_run(_py(3, "<module>"), PY)) == (None, False)


def test_python_decorator_line_resolves_to_the_decorated_function() -> None:
    assert _hit(_run(_py(38, "route"), PY, PY_SOURCE)) == (PY[2], False)
    assert _hit(_run(_py(39, "route"), PY, PY_SOURCE)) == (PY[2], False)


def test_python_decorator_line_still_needs_the_name() -> None:
    res = _run(_py(38, "other"), PY, PY_SOURCE)
    assert res.skipped == {"no_span": 1}


def test_python_decorator_rule_needs_a_decorator_line() -> None:
    # Line 36 is blank, not a decorator: the function below is not its symbol.
    res = _run(_py(36, "route"), PY, PY_SOURCE)
    assert res.skipped == {"no_span": 1}


def test_python_bare_lambda_without_a_qualname_is_not_guessed() -> None:
    # Old interpreters give co_name only: "<lambda>" alone could sit anywhere.
    res = _run(_py(8, "<lambda>"), PY)
    assert res.skipped == {"second_signal_mismatch": 1}


# -- Go ------------------------------------------------------------------------------

GO = [_sym("cmd.go", "cmd.Command.Execute", 10, 40, "method"), _sym("cmd.go", "cmd.T", 50, 55, "struct")]


def _go(line: int, start: int | None, name: str) -> ProfileFrame:
    return ProfileFrame(f"{ROOT}/cmd.go", line, "go", function=name, start_line=start)


def test_go_closures_fold_into_the_enclosing_function() -> None:
    for name in (
        "example.com/cmd.(*Command).Execute.func1",
        "example.com/cmd.(*Command).Execute.func1.2",
        "example.com/cmd.(*Command).Execute.gowrap1",
        "example.com/cmd.(*Command).Execute.deferwrap1",
        "example.com/cmd.(*Command[go.shape.int]).Execute.func3",
    ):
        assert _hit(_run(_go(22, 20, name), GO)) == (GO[0], True), name


def test_go_closure_start_outside_the_parent_is_a_mismatch() -> None:
    res = _run(_go(22, 5, "example.com/cmd.(*Command).Execute.func1"), GO)
    assert res.skipped == {"second_signal_mismatch": 1}


def test_go_package_initializer_attributes_to_the_file() -> None:
    assert _hit(_run(_go(3, 3, "example.com/cmd.init"), GO)) == (None, False)
    assert _hit(_run(_go(4, 4, "example.com/cmd.init.func1"), GO)) == (None, True)


def test_go_user_init_function_outside_a_span_is_no_span() -> None:
    assert _run(_go(3, 3, "example.com/cmd.init.0"), GO).skipped == {"no_span": 1}


# -- JavaScript ----------------------------------------------------------------------

JS = [_sym("lib/app.js", "app.createApp", 3, 12)]
JS_SOURCE = [
    "'use strict';",
    "",
    "function createApp(options) {",
    "  const app = {};",
    "  app.handle = function handle(req) {",
    "    return [req].map(r => r.url);",
    "  };",
    "  return app;",
    "",
    "",
    "  // end",
    "}",
]


def _js(line: int, column: int, name: str = "") -> ProfileFrame:
    return ProfileFrame(f"{ROOT}/lib/app.js", line, "javascript", function=name, column=column)


def test_javascript_function_starting_in_a_body_folds_into_it() -> None:
    column = JS_SOURCE[4].index("(") + 1
    assert _hit(_run(_js(5, column, "handle"), JS, JS_SOURCE)) == (JS[0], True)
    column = JS_SOURCE[5].index("r =>") + 1
    assert _hit(_run(_js(6, column), JS, JS_SOURCE)) == (JS[0], True)


def test_javascript_module_wrapper_attributes_to_the_file() -> None:
    assert _hit(_run(_js(1, 1), JS, JS_SOURCE)) == (None, False)


def test_javascript_named_function_at_one_one_is_not_the_wrapper() -> None:
    assert _run(_js(1, 1, "main"), JS, JS_SOURCE).skipped == {"no_span": 1}


def test_javascript_function_on_the_last_line_is_not_folded() -> None:
    lines = [*JS_SOURCE[:11], "}, function after() {"]
    assert _run(_js(12, 4), JS, lines).skipped == {"second_signal_mismatch": 1}


def test_javascript_function_in_the_signature_is_not_folded() -> None:
    syms = [_sym("lib/app.js", "app.f", 1, 4)]
    lines = ["function f(", "  cb = () => 1,", ") {", "}"]
    assert _run(_js(2, 8), syms, lines).skipped == {"second_signal_mismatch": 1}


def test_javascript_callback_on_a_one_line_function_is_not_folded() -> None:
    syms = [_sym("lib/app.js", "app.f", 1, 1)]
    lines = ["function f(xs) { return xs.map(x => x); }"]
    column = lines[0].index("x =>") + 1
    assert _run(_js(1, column), syms, lines).skipped == {"second_signal_mismatch": 1}


def test_javascript_fold_needs_source() -> None:
    assert _run(_js(6, 5), JS).skipped == {"no_source": 1}


# -- review cases ------------------------------------------------------------------


def test_python_comprehensions_fold() -> None:
    for name in ("App.run.<locals>.<listcomp>", "App.run.<locals>.<dictcomp>", "App.run.<locals>.<setcomp>"):
        assert _hit(_run(_py(8, name), PY)) == (PY[1], True), name


def test_python_folds_into_the_nearest_indexed_scope() -> None:
    g = _sym("app.py", "app.App.run.g", 7, 12)
    assert _hit(_run(_py(9, "App.run.<locals>.g.<locals>.h"), [*PY, g])) == (g, True)


def test_python_bare_genexpr_in_a_class_is_not_the_file() -> None:
    assert _run(_py(25, "<genexpr>"), PY).skipped == {"second_signal_mismatch": 1}


def test_python_stacked_and_class_decorators() -> None:
    syms = [_sym("app.py", "app.Widget", 4, 10, "class"), _sym("app.py", "app.Widget.draw", 7, 9, "method")]
    lines = ["", "", "@dataclass", "class Widget:", "", "    @staticmethod", "    def draw():", "        pass", "", ""]
    lines[1] = "@register"
    assert _hit(_run(_py(2, "Widget"), syms, lines)) == (syms[0], False)
    assert _hit(_run(_py(3, "Widget"), syms, lines)) == (syms[0], False)
    assert _hit(_run(_py(6, "Widget.draw"), syms, lines)) == (syms[1], False)


def test_go_same_line_closure_is_marked_folded() -> None:
    assert _hit(_run(_go(10, 10, "example.com/cmd.(*Command).Execute.func1"), GO)) == (GO[0], True)


def test_go_method_named_init_is_not_the_package_initializer() -> None:
    assert _run(_go(3, 3, "example.com/cmd.(*T).init"), GO).skipped == {"no_span": 1}


def test_javascript_reads_each_file_once() -> None:
    calls = []

    def read(path: str):
        calls.append(path)
        return JS_SOURCE

    frames = [_js(6, 15), _js(6, 15, "x"), _js(5, 23, "handle")]
    resolve_frames(frames, root=ROOT, symbols_by_file={"lib/app.js": JS}, read_lines=read)
    assert calls == ["lib/app.js"]
