"""Source windows served by get_answer in place of the evidence rows."""

from __future__ import annotations

from pathlib import Path

from repowise.server.mcp_server.tool_answer.data_shape import _is_data_shape_question
from repowise.server.mcp_server.tool_answer.symbols import _extract_question_identifiers
from repowise.server.mcp_server.tool_answer.windows import (
    WINDOW_MAX_LINES,
    SourcePlan,
    _rerank,
    _Sym,
    allocate,
    continuation_names,
    delivered_line,
    plan_files,
    ranked_files,
)


def _write(root: Path, rel: str, lines: list[str]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _module(n_helpers: int = 3) -> tuple[list[str], list[_Sym]]:
    """A file with a long ``main`` and short helpers after a wide gap."""
    lines = ["import os", ""]
    syms: list[_Sym] = []
    start = len(lines) + 1
    lines += ["def main():"] + [f"    step_{i}()" for i in range(120)]
    syms.append(_Sym("main", "main", "function", start, len(lines), None))
    for i in range(n_helpers):
        lines += ["", ""]
        s = len(lines) + 1
        lines += [f"def helper_{i}():", "    return 1"]
        syms.append(_Sym(f"helper_{i}", f"helper_{i}", "function", s, s + 1, None))
    lines += [""] * 30
    s = len(lines) + 1
    lines += ["def tail():", "    return 2"]
    syms.append(_Sym("tail", "tail", "function", s, s + 1, None))
    return lines, syms


def _plan(tmp_path: Path, raw: dict, question: str, lines, syms, path="pkg/mod.py"):
    _write(tmp_path, path, lines)
    plans, drifted = plan_files(
        raw, question, _extract_question_identifiers(question), tmp_path, {path: syms}, [path]
    )
    return plans, drifted


def test_a_window_runs_from_the_definition_and_is_capped(tmp_path):
    lines, syms = _module()
    raw = {"symbol_bodies": [{"path": "pkg/mod.py", "lines": [3, 123]}]}
    plans, _ = _plan(tmp_path, raw, "how does main work", lines, syms)
    served, _ = allocate(plans, 100_000)
    a, b = served[0].ranges[0]
    assert a == 3 and b - a + 1 == WINDOW_MAX_LINES
    assert served[0].code.startswith("3\tdef main():")


def test_a_gap_marker_names_the_definitions_it_skips(tmp_path):
    lines, syms = _module()
    tail = syms[-1]
    raw = {"symbol_bodies": [{"path": "pkg/mod.py", "lines": [3, 123]}]}
    question = f"how does main reach tail at line {tail.start}"
    plans, _ = _plan(tmp_path, raw, question, lines, syms)
    plans[0].windows.append(type(plans[0].windows[0])(tail.start, tail.end, 1, ["tail"]))
    served, _ = allocate(plans, 100_000)
    code = served[0].code
    assert "not shown: helper_0, helper_1, helper_2" in code
    assert f"{tail.start}\tdef tail():" in code
    text, names = delivered_line(served, [], [])
    assert "pkg/mod.py::helper_0" in names


def test_the_statement_claims_only_what_was_rendered(tmp_path):
    lines, syms = _module()
    raw = {"symbol_bodies": [{"path": "pkg/mod.py", "lines": [3, 123]}]}
    plans, _ = _plan(tmp_path, raw, "how does main work", lines, syms)
    served, not_shown = allocate(plans, 900)
    text, names = delivered_line(served, not_shown, [])
    a, b = served[0].ranges[0]
    assert f"pkg/mod.py {a}-{b} (main cut after line {b})" in text
    assert b < 82
    assert names == ["pkg/mod.py::main"]
    # Never sends the agent to a Read; the next step is the same tool, and the
    # names it offers are ones a continuation accepts.
    assert "Read the" not in text and "get_answer" in text
    assert continuation_names(" ".join(names)) == [("pkg/mod.py", "main")]


def test_a_drifted_file_is_sent_whole_or_left_out(tmp_path):
    lines, syms = _module()
    moved = [_Sym(s.name, s.qualified, s.kind, s.start + 5, s.end + 5, None) for s in syms]
    raw = {"symbol_bodies": [{"path": "pkg/mod.py", "lines": [8, 30]}]}
    plans, drifted = _plan(tmp_path, raw, "how does main work", lines, moved)
    assert drifted == ["pkg/mod.py"] and plans[0].whole
    served, not_shown = allocate(plans, 100)
    assert not served and not_shown == ["pkg/mod.py"]
    text, _ = delivered_line(served, not_shown, drifted)
    assert "Changed since the index" in text


def test_keyless_files_rerank_by_the_names_they_define():
    syms = {
        "a/noise.py": [_Sym("render", "render", "function", 1, 2, None)],
        "a/session.py": [_Sym("save_session", "save_session", "function", 1, 2, None)],
    }
    ranked = _rerank(["a/noise.py", "a/session.py"], "Where is the session cookie saved?", syms)
    assert ranked[0][0] == "a/session.py"


def test_a_synthesized_answer_leads_with_its_citations():
    raw = {
        "citations": ["b.py"],
        "retrieval": [{"path": "a.py", "score": 3.0}, {"path": "b.py", "score": 1.0}],
    }
    leading, pool, scores = ranked_files(raw)
    assert leading == ["b.py"] and pool == ["a.py"] and scores["a.py"] == 3.0


def test_continuation_takes_only_names():
    assert continuation_names("pkg/mod.py::main helper_0") == [
        ("pkg/mod.py", "main"),
        (None, "helper_0"),
    ]
    assert continuation_names("Store.write") == [(None, "Store.write")]
    assert continuation_names("how does main work") is None
    assert continuation_names("session") is None


def test_the_plan_serves_within_its_budget(tmp_path):
    lines, syms = _module()
    raw = {"symbol_bodies": [{"path": "pkg/mod.py", "lines": [3, 123]}]}
    plans, drifted = _plan(tmp_path, raw, "how does main work", lines, syms)
    source, text = SourcePlan(plans, drifted).serve(1_500)
    assert source[0]["path"] == "pkg/mod.py"
    assert len(source[0]["code"].encode()) + len(text.encode()) <= 1_500


def test_a_risk_question_is_not_a_data_shape_question():
    q = "Assess the risk of changing how the `Headers` class normalizes or stores header keys."
    assert not _is_data_shape_question(q, _extract_question_identifiers(q))
    q = "What keys does the meta dict in build_meta carry?"
    assert _is_data_shape_question(q, _extract_question_identifiers(q))


def test_named_members_lead_over_their_named_class(tmp_path):
    lines = ["class Box:"] + [f"    x{i} = {i}" for i in range(100)]
    start = len(lines) + 1
    lines += ["    def fill(self):"] + [f"        self.x{i} += 1" for i in range(10)]
    syms = [
        _Sym("Box", "Box", "class", 1, len(lines), None),
        _Sym("fill", "Box.fill", "method", start, len(lines), "Box"),
    ]
    plans, _ = _plan(tmp_path, {}, "how does Box.fill work", lines, syms)
    served, _ = allocate(plans, 600)
    assert served[0].ranges == [(start, len(lines))], "the class head took the slot"


def test_named_definitions_that_do_not_fit_are_listed(tmp_path):
    lines: list[str] = []
    syms: list[_Sym] = []
    for name in ("load_rows", "save_rows"):
        start = len(lines) + 1
        lines += [f"def {name}():"] + [f"    step_{i}(rows, columns, options, callback, retries)" for i in range(60)] + [""] * 20
        syms.append(_Sym(name, name, "function", start, start + 60, None))
    plans, drifted = _plan(tmp_path, {}, "how do load_rows and save_rows work", lines, syms)
    served, not_shown = allocate(plans, 1_600)
    text, names = delivered_line(served, not_shown, drifted)
    assert "not sent: save_rows" in text
    assert "pkg/mod.py::save_rows" in names


def test_long_docstrings_and_comments_keep_their_first_lines():
    from repowise.server.mcp_server.tool_answer.windows import _prose_mask, _render_lines

    lines = ["def f():", '    """Doc line one.', *["    more prose"] * 8, '    """', "    # a", "    return 1"]
    code = _render_lines(lines, 1, len(lines), _prose_mask("m.py", lines))
    assert "2\t" in code and "3\t" in code and "4\t" not in code
    assert "... lines 4-12 not shown (docstring or comment)" in code
    assert code.endswith("13\t    return 1")
    # A C preprocessor line is code, not a comment.
    assert _prose_mask("m.c", ["#include <stdio.h>"]) == [False]
