"""Profile frames to indexed symbols: every skip has a reason, nothing is guessed."""

from __future__ import annotations

from repowise.core.frame_resolver import ProfileFrame, resolve_frames
from repowise.core.workspace.repo_index import IndexedSymbol

ROOT = "C:/work/repo"


def _sym(path: str, qualified: str, start: int, end: int, kind: str = "function") -> IndexedSymbol:
    name = qualified.rsplit(".", 1)[-1]
    return IndexedSymbol(
        symbol_id=f"{path}::{name}",
        name=name,
        qualified_name=qualified,
        kind=kind,
        signature="",
        file_path=path,
        start_line=start,
        end_line=end,
        visibility="public",
    )


PY_CLASS = _sym("pkg/app.py", "pkg.app.App", 1, 40, "class")
PY_INIT = _sym("pkg/app.py", "pkg.app.App.__init__", 3, 10, "method")
PY_RUN = _sym("pkg/app.py", "pkg.app.App.run", 12, 30, "method")
GO_FLAGS = _sym("command.go", "command.Command.Flags", 100, 110, "method")
GO_PARENT = _sym("command.go", "command.Command.HasParent", 120, 122, "method")
JS_SEND = _sym("lib/response.js", "response.send", 5, 20)
JS_TWIN_A = _sym("lib/twins.js", "twins.a", 1, 1)
JS_TWIN_B = _sym("lib/twins.js", "twins.b", 1, 1)

SYMBOLS = {
    "pkg/app.py": [PY_CLASS, PY_INIT, PY_RUN],
    "pkg/empty.py": [],
    "command.go": [GO_FLAGS, GO_PARENT],
    "lib/response.js": [JS_SEND],
    "lib/twins.js": [JS_TWIN_A, JS_TWIN_B],
    "gen/api_pb2.py": [_sym("gen/api_pb2.py", "gen.api_pb2.f", 1, 5)],
}

JS_SOURCE = {
    "lib/response.js": [""] * 4
    + ["function send(body) { return [1].map(x => x); }"]
    + [""] * 20,
    "lib/twins.js": ["function a() {} function b() {}"],
}


def _resolve(*frames: ProfileFrame, **kw):
    kw.setdefault("root", ROOT)
    kw.setdefault("symbols_by_file", SYMBOLS)
    kw.setdefault("read_lines", JS_SOURCE.get)
    return resolve_frames(frames, **kw)


def _py(line: int, name: str, path: str = "pkg/app.py") -> ProfileFrame:
    return ProfileFrame(f"{ROOT}/{path}", line, "python", function=name)


def _one(resolution):
    assert len(resolution.resolved) == 1, resolution.reasons
    return resolution.resolved[0].symbol


# -- paths ---------------------------------------------------------------


def test_python_frame_under_root_resolves_by_qualname() -> None:
    assert _one(_resolve(_py(5, "App.__init__"))) is PY_INIT


def test_frame_under_another_checkout_is_outside_even_when_the_suffix_matches() -> None:
    other = ProfileFrame("C:/bench/repo/pkg/app.py", 5, "python", function="App.__init__")
    assert _resolve(other).skipped == {"outside_root": 1}


def test_sibling_directory_sharing_the_root_prefix_is_outside() -> None:
    sibling = ProfileFrame("C:/work/repo2/pkg/app.py", 5, "python", function="App.__init__")
    assert _resolve(sibling).skipped == {"outside_root": 1}


def test_relative_frame_path_is_outside() -> None:
    rel = ProfileFrame("pkg/app.py", 5, "python", function="App.__init__")
    assert _resolve(rel).skipped == {"outside_root": 1}


def test_dot_dot_escape_from_the_root_is_outside() -> None:
    escape = ProfileFrame(f"{ROOT}/../repo2/pkg/app.py", 5, "python", function="App.__init__")
    assert _resolve(escape).skipped == {"outside_root": 1}


def test_windows_paths_compare_without_case_and_take_the_index_spelling() -> None:
    frame = ProfileFrame(r"c:\WORK\repo\PKG\app.py", 5, "python", function="App.__init__")
    res = _resolve(frame)
    assert res.resolved[0].path == "pkg/app.py"


def test_posix_root_compares_with_case() -> None:
    syms = {"a.py": [_sym("a.py", "a.f", 1, 3)]}
    frame = ProfileFrame("/Home/repo/a.py", 2, "python", function="f")
    assert resolve_frames([frame], root="/home/repo", symbols_by_file=syms).skipped == {
        "outside_root": 1
    }
    ok = ProfileFrame("/home/repo/a.py", 2, "python", function="f")
    assert len(resolve_frames([ok], root="/home/repo", symbols_by_file=syms).resolved) == 1


def test_file_url_frame_is_decoded() -> None:
    frame = ProfileFrame(
        "file:///C:/work/repo/lib/response.js", 5, "javascript", function="send", column=14
    )
    assert _one(_resolve(frame)) is JS_SEND


# -- file-level skips ------------------------------------------------------


def test_dirty_file_is_skipped() -> None:
    res = _resolve(_py(5, "App.__init__"), dirty_paths={"pkg/app.py"})
    assert res.skipped == {"dirty": 1}


def test_generated_file_is_skipped_from_the_stored_origin() -> None:
    res = _resolve(_py(5, "App.__init__"), origins={"pkg/app.py": "generated"})
    assert res.skipped == {"generated": 1}


def test_generated_file_is_skipped_by_path_origin_without_a_stored_one() -> None:
    res = _resolve(_py(2, "f", path="gen/api_pb2.py"))
    assert res.skipped == {"generated": 1}


def test_stored_origin_wins_over_the_path() -> None:
    res = _resolve(_py(2, "f", path="gen/api_pb2.py"), origins={"gen/api_pb2.py": "production"})
    assert len(res.resolved) == 1


def test_dependency_inside_the_checkout_is_vendored() -> None:
    frame = ProfileFrame(f"{ROOT}/node_modules/qs/lib/parse.js", 3, "javascript", column=1)
    assert _resolve(frame).skipped == {"vendored": 1}


def test_file_the_index_lacks_is_not_indexed() -> None:
    assert _resolve(_py(5, "f", path="pkg/new.py")).skipped == {"not_indexed": 1}


def test_line_below_one_is_bad() -> None:
    assert _resolve(_py(0, "App.__init__")).skipped == {"bad_line": 1}


def test_unknown_language_is_unsupported() -> None:
    frame = ProfileFrame(f"{ROOT}/pkg/app.py", 5, "ruby", function="x")  # type: ignore[arg-type]
    assert _resolve(frame).skipped == {"unsupported_language": 1}


# -- Python ------------------------------------------------------------------


def test_python_named_frame_outside_every_symbol_is_no_span() -> None:
    assert _resolve(_py(2, "helper", path="pkg/empty.py")).skipped == {"no_span": 1}


def test_python_qualname_mismatch_is_skipped() -> None:
    assert _resolve(_py(5, "Other.__init__")).skipped == {"second_signal_mismatch": 1}


def test_python_bare_name_matches_the_tail_only_at_a_dot() -> None:
    assert _one(_resolve(_py(13, "run"))) is PY_RUN
    assert _resolve(_py(13, "un")).skipped == {"second_signal_mismatch": 1}


def test_python_nested_def_under_the_wrong_parent_is_a_mismatch() -> None:
    res = _resolve(_py(13, "App.other.<locals>.inner"))
    assert res.skipped == {"second_signal_mismatch": 1}


def test_python_frame_without_a_name_has_no_second_signal() -> None:
    assert _resolve(_py(5, "")).skipped == {"no_second_signal": 1}


# -- Go ----------------------------------------------------------------------


def _go(line: int, start: int | None, name: str = "cobra.(*Command).Flags") -> ProfileFrame:
    return ProfileFrame(f"{ROOT}/command.go", line, "go", function=name, start_line=start)


def test_go_resolves_when_start_lines_agree() -> None:
    assert _one(_resolve(_go(104, 100))) is GO_FLAGS


def test_go_inline_mislabel_is_caught_by_the_start_line() -> None:
    # pprof names CommandPath (starting at 90) at a line inside HasParent.
    res = _resolve(_go(121, 90, "cobra.(*Command).CommandPath"))
    assert res.skipped == {"second_signal_mismatch": 1}


def test_go_closure_of_another_function_is_a_mismatch() -> None:
    res = _resolve(_go(105, 104, "cobra.(*Command).Other.func1"))
    assert res.skipped == {"second_signal_mismatch": 1}


def test_go_without_start_line_has_no_second_signal() -> None:
    assert _resolve(_go(104, None)).skipped == {"no_second_signal": 1}


def test_go_never_joins_by_name() -> None:
    # Right name, wrong start line: skipped.
    assert _resolve(_go(104, 101)).skipped == {"second_signal_mismatch": 1}


# -- JavaScript --------------------------------------------------------------


def _js(line: int, column: int | None, name: str = "", path: str = "lib/response.js"):
    return ProfileFrame(f"{ROOT}/{path}", line, "javascript", function=name, column=column)


def test_javascript_joins_by_start_line_and_column() -> None:
    # "function send(" : the parameter list opens at column 14.
    assert _one(_resolve(_js(5, 14))) is JS_SEND


def test_javascript_ignores_the_name() -> None:
    assert _one(_resolve(_js(5, 14, name="totallyDifferent"))) is JS_SEND


def test_javascript_nested_function_on_the_start_line_is_a_mismatch() -> None:
    # The arrow "x => x" starts after the body opened.
    column = JS_SOURCE["lib/response.js"][4].index("x =>") + 1
    assert _resolve(_js(5, column)).skipped == {"second_signal_mismatch": 1}


def test_javascript_function_outside_every_symbol_is_no_span() -> None:
    syms = {"lib/y.js": [_sym("lib/y.js", "y.f", 10, 12)]}
    frame = ProfileFrame(f"{ROOT}/lib/y.js", 3, "javascript", column=5)
    res = resolve_frames(
        [frame], root=ROOT, symbols_by_file=syms, read_lines=lambda _p: ["x"] * 12
    )
    assert res.skipped == {"no_span": 1}


def test_javascript_two_functions_starting_on_one_line_are_ambiguous() -> None:
    assert _resolve(_js(1, 11, path="lib/twins.js")).skipped == {"ambiguous": 1}


def test_javascript_without_column_has_no_second_signal() -> None:
    assert _resolve(_js(5, None)).skipped == {"no_second_signal": 1}


def test_javascript_without_source_is_skipped() -> None:
    assert _resolve(_js(5, 14), read_lines=None).skipped == {"no_source": 1}


# -- result shape --------------------------------------------------------------


def test_reasons_align_with_input_and_counts_hold_only_reasons_seen() -> None:
    res = _resolve(_py(5, "App.__init__"), _py(5, "Nope"), _go(104, 100))
    assert res.reasons == (None, "second_signal_mismatch", None)
    assert [r.index for r in res.resolved] == [0, 2]
    assert res.skipped == {"second_signal_mismatch": 1}


def test_no_frames_is_empty_not_zero() -> None:
    res = _resolve()
    assert res.resolved == () and res.skipped == {}


# -- review cases ----------------------------------------------------------------


def _js_line(text: str, column: int, start: int = 1):
    sym = _sym("lib/x.js", "x.handler", start, start + 3)
    lines = [""] * (start - 1) + [text, "", "", ""]
    frame = ProfileFrame(f"{ROOT}/lib/x.js", start, "javascript", column=column)
    return resolve_frames(
        [frame], root=ROOT, symbols_by_file={"lib/x.js": [sym]}, read_lines=lambda _p: lines
    )


def test_javascript_unindexed_function_earlier_on_the_line_is_not_the_symbol() -> None:
    text = "items.map(x => x.id); function handler() {}"
    assert _js_line(text, text.index("x =>") + 1).skipped == {"second_signal_mismatch": 1}
    assert _js_line(text, text.index("() {}") + 1).skipped == {"second_signal_mismatch": 1}


def test_javascript_shapes_a_line_declares() -> None:
    for text in (
        "app.handle = function handle(req, res) {",
        "  get: function getrouter() {",
        "const handler = (a, b) => {",
        "const handler = async x => x + 1",
        "  static async *handler(a) {",
        "export default function (req) {",
        "app[method] = function (path) {",
    ):
        column = text.index("x =>") + 1 if "x =>" in text else text.index("(") + 1
        assert len(_js_line(text, column).resolved) == 1, text


def test_javascript_string_before_the_function_is_skipped_not_guessed() -> None:
    text = 'const s = "{"; function handler() {}'
    assert _js_line(text, text.index("() {}") + 1).skipped == {"second_signal_mismatch": 1}


def test_javascript_columns_count_utf16_units() -> None:
    text = "const \U0001d465 = function () {"
    # The astral letter takes two UTF-16 units, so V8's column is one past the code point index.
    column = text.index("(") + 2
    assert len(_js_line(text, column).resolved) == 1


def test_javascript_column_past_the_line_is_a_mismatch() -> None:
    assert _js_line("function handler() {}", 99).skipped == {"second_signal_mismatch": 1}


def test_drive_root_without_slash_and_unc_roots_fold_case() -> None:
    syms = {"a.py": [_sym("a.py", "a.f", 1, 3)]}
    frame = ProfileFrame("C:/A.py", 2, "python", function="f")
    assert len(resolve_frames([frame], root="C:", symbols_by_file=syms).resolved) == 1
    unc = ProfileFrame(r"\\SRV\share\repo\a.py", 2, "python", function="f")
    assert len(resolve_frames([unc], root="//srv/share/repo", symbols_by_file=syms).resolved) == 1


def test_file_url_scheme_is_case_insensitive() -> None:
    frame = ProfileFrame("FILE:///C:/work/repo/pkg/app.py", 5, "python", function="App.__init__")
    assert _one(_resolve(frame)) is PY_INIT


def test_dirty_paths_are_normalised() -> None:
    assert _resolve(_py(5, "App.__init__"), dirty_paths={"./pkg/app.py"}).skipped == {"dirty": 1}


def test_dirty_wins_over_generated() -> None:
    res = _resolve(_py(5, "App.__init__"), dirty_paths={"pkg/app.py"}, origins={"pkg/app.py": "generated"})
    assert res.skipped == {"dirty": 1}


def test_go_span_lookup_sees_functions_only() -> None:
    syms = {"t.go": [_sym("t.go", "t.T", 10, 20, "struct"), _sym("t.go", "t.f", 30, 40)]}
    frame = ProfileFrame(f"{ROOT}/t.go", 12, "go", function="t.x", start_line=10)
    assert resolve_frames([frame], root=ROOT, symbols_by_file=syms).skipped == {"no_span": 1}


def test_a_frame_outside_the_root_is_outside_whatever_its_language() -> None:
    frame = ProfileFrame("C:/Python/lib/json.py", 5, "unknown", function="x")  # type: ignore[arg-type]
    assert _resolve(frame).skipped == {"outside_root": 1}


def test_frame_paths_lists_only_files_under_the_root() -> None:
    from repowise.core.frame_resolver import frame_paths

    frames = [
        ProfileFrame(f"{ROOT}/pkg/app.py", 5, "python"),
        ProfileFrame("C:/work/other/pkg/app.py", 5, "python"),
        ProfileFrame("<built-in>", 0, "python"),
    ]
    assert frame_paths(frames, root=ROOT) == {"pkg/app.py"}
