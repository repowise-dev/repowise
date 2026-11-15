"""Whole-file ``cat`` compaction and the folded diff rendering.

Covers the ``file_read`` filter and the hook gate that admits it, the repeat
rule (an identical command right after a rewrite runs raw), the diff
renderer's folding, and the router sending any diff to that renderer.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys

import pytest

from repowise.cli import rewrite_hook
from repowise.cli.rewrite_hook import classify, decide
from repowise.core.distill.cat_target import resolve_cat_path
from repowise.core.distill.engine import distill_output
from repowise.core.distill.filters import file_read
from repowise.core.distill.filters.git_diff import MAX_RENDER_CHARS, GitDiffFilter
from repowise.core.distill.router import select_filter
from repowise.core.distill.store import OmissionStore

# -- fixtures -----------------------------------------------------------------


def _source(functions: int = 80, body: int = 5) -> str:
    lines = ['"""A module."""', "", "import os", ""]
    for i in range(functions):
        lines.append(f"def func_{i}(arg):")
        lines.extend(f"    value_{j} = os.path.join(arg, 'part_{j}')" for j in range(body))
        lines.append("")
    return "\n".join(lines) + "\n"


def _symbols(text: str) -> list[tuple[str, str, int, int]]:
    rows = []
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        if line.startswith("def "):
            name = line[4:].split("(")[0]
            rows.append((name, "function", number, number + 5))
    return rows


def _index(repo, rel: str, symbols) -> None:
    db = repo / ".repowise" / "wiki.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE IF NOT EXISTS wiki_symbols "
        "(file_path TEXT, name TEXT, kind TEXT, start_line INT, end_line INT, signature TEXT)"
    )
    con.executemany(
        "INSERT INTO wiki_symbols VALUES (?, ?, ?, ?, ?, '')",
        [(rel, name, kind, start, end) for name, kind, start, end in symbols],
    )
    con.commit()
    con.close()


@pytest.fixture
def indexed_repo(tmp_path):
    repo = tmp_path / "proj"
    (repo / ".repowise").mkdir(parents=True)
    (repo / "src").mkdir()
    text = _source()
    (repo / "src" / "mod.py").write_text(text, encoding="utf-8", newline="\n")
    _index(repo, "src/mod.py", _symbols(text))
    (repo / "src" / "small.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "notes.md").write_text("words\n" * 2000, encoding="utf-8")
    (repo / "src" / "wide.py").write_text(("x = '" + "y" * 200 + "'\n") * 50, encoding="utf-8")
    _index(repo, "src/wide.py", [("x", "variable", 1, 1)])
    nested = repo / "nested"
    (nested / ".repowise").mkdir(parents=True)
    (nested / "src").mkdir()
    (nested / "src" / "mod.py").write_text(text, encoding="utf-8", newline="\n")
    _index(nested, "src/mod.py", _symbols(text))
    return repo


def _diff_block(path: str, hunks: int = 3, context: int = 3) -> list[str]:
    out = [f"diff --git a/{path} b/{path}", "index 1111111..2222222 100644"]
    out += [f"--- a/{path}", f"+++ b/{path}"]
    for h in range(hunks):
        start = 10 + h * 40
        out.append(f"@@ -{start},{2 * context + 1} +{start},{2 * context + 1} @@ def f{h}():")
        out += [f"     ctx_before_{h}_{i} = {i}" for i in range(context)]
        out += [f"-    old_{h} = compute({h})", f"+    new_{h} = compute({h}, fast=True)"]
        out += [f"     ctx_after_{h}_{i} = {i}" for i in range(context)]
    return out


# -- diff renderer ------------------------------------------------------------


def test_diff_folds_context_and_keeps_every_change() -> None:
    raw_lines = []
    for n in range(12):
        raw_lines += _diff_block(f"pkg/mod_{n}.py", hunks=4, context=6)
    raw = "\n".join(raw_lines)
    out = GitDiffFilter().distill(raw, command="git diff")
    kept = set(out.splitlines())
    for line in raw_lines:
        if line.startswith(("+", "-", "@@", "diff --git")) and not line.startswith(("+++", "---")):
            assert line in kept, line
    assert "  ... 6 unchanged lines" in kept
    assert not any(line.startswith("index ") for line in kept)
    assert len(out) < len(raw) / 2


def test_lockfile_folds_to_one_line() -> None:
    raw_lines = _diff_block("src/app.py") + _diff_block("package-lock.json", hunks=60)
    out = GitDiffFilter().distill("\n".join(raw_lines), command="git diff")
    assert "-    old_0 = compute(0)" in out
    assert "  ... lockfile or generated: +60 -60" in out
    assert "compute(59" not in out


def test_small_diff_passes_through() -> None:
    raw = "\n".join(_diff_block("a.py"))
    assert len(raw) < 5_000
    with pytest.raises(ValueError):
        GitDiffFilter().distill(raw, command="git diff")


def test_large_diff_stays_in_budget_and_lists_every_file() -> None:
    raw_lines = []
    for n in range(40):
        raw_lines += _diff_block(f"pkg/big_{n}.py", hunks=12, context=3)
    out = GitDiffFilter().distill("\n".join(raw_lines), command="git diff")
    assert len(out) <= MAX_RENDER_CHARS + 200
    for n in range(40):
        assert f"pkg/big_{n}.py" in out


def test_lines_after_a_hunk_are_kept_verbatim() -> None:
    raw_lines = ["commit abc", "Author: A <a@b>", ""]
    for n in range(10):
        raw_lines += _diff_block(f"m{n}.py", hunks=4, context=5)
        raw_lines += [f"commit def{n}", "    message line"]
    out = GitDiffFilter().distill("\n".join(raw_lines), command="git log -p")
    assert "commit abc" in out
    assert "    message line" in out


def test_router_sends_log_patch_and_trailing_diffs_to_the_diff_renderer() -> None:
    diff = "\n".join(_diff_block("a.py"))
    assert select_filter("git log -p -3", diff).name == "git_diff"
    assert select_filter("git log --oneline -3", "abc1234 fix\n" * 20).name == "git_log"
    chained = "abc1234 fix one\ndef5678 fix two\n" + diff
    assert select_filter("git log --oneline -2 && git diff", chained).name == "git_diff"


# -- file_read filter ---------------------------------------------------------


def test_render_outline_and_head(indexed_repo) -> None:
    text = (indexed_repo / "src" / "mod.py").read_text(encoding="utf-8")
    out = file_read.render("src/mod.py", text, _symbols(text))
    assert out.startswith("src/mod.py: ")
    assert "function func_0" in out
    assert '"""A module."""' in out
    assert len(out) <= file_read.MAX_RENDER_CHARS
    assert "read a range with sed -n" in out


def test_render_refuses_a_stale_index(indexed_repo) -> None:
    text = (indexed_repo / "src" / "mod.py").read_text(encoding="utf-8")
    shifted = "\n" * 7 + text
    with pytest.raises(ValueError):
        file_read.render("src/mod.py", shifted, _symbols(text))


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cat src/mod.py", "src/mod.py"),
        ("cd src && cat mod.py", "src/mod.py"),
        ("cd src; cat mod.py", "src/mod.py"),
        ('cat "src/mod.py"', "src/mod.py"),
        ("cat src/mod.py | head", None),
        ("cat a.py b.py", None),
        ("cat -n src/mod.py", None),
        ("echo x && cat src/mod.py", None),
    ],
)
def test_resolve_cat_path(tmp_path, command, expected) -> None:
    got = resolve_cat_path(command, str(tmp_path))
    if expected is None:
        assert got is None
    else:
        assert got == str(tmp_path / expected)


def test_cat_distills_and_expands_byte_exact(indexed_repo, monkeypatch) -> None:
    monkeypatch.chdir(indexed_repo)
    raw = (indexed_repo / "src" / "mod.py").read_text(encoding="utf-8")
    store = OmissionStore(indexed_repo / ".repowise" / "omissions" / "omissions.db")
    try:
        result = distill_output(raw, command="cd src && cat mod.py", store=store)
        assert result.distilled and result.filter_name == "file_read"
        assert len(result.text) < 4_096
        assert store.get(result.ref) == raw
    finally:
        store.close()


def test_cat_of_an_unindexed_file_is_raw(indexed_repo, monkeypatch) -> None:
    monkeypatch.chdir(indexed_repo)
    raw = (indexed_repo / "notes.md").read_text(encoding="utf-8")
    result = distill_output(raw, command="cat notes.md")
    assert not result.distilled and result.text == raw


# -- hook ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "family"),
    [
        ("cat src/mod.py", "file_read"),
        ("cd src && cat mod.py", "file_read"),
        ("cd src; cat mod.py", "file_read"),
        ("cd $X && cat mod.py", None),
        ("cd src && cat mod.py | head", None),
        ("cat server.log", "logs"),
        ("cd src && cat a.py b.py", None),
        ("git log -p -3", "git_diff"),
        ("git log --oneline -3", "git_log"),
    ],
)
def test_classify(command, family) -> None:
    assert classify(command) == family


@pytest.mark.parametrize(
    ("command", "rewritten"),
    [
        ("cat src/mod.py", True),
        ("cd src && cat mod.py", True),
        ("cat src/small.py", False),  # under 5 KB
        ("cat notes.md", False),  # no indexed symbols
        ("cat src/mod.py | head", False),  # a pipe: the agent already bounded it
        ("cat src/missing.py", False),
        ("cat src/wide.py", False),  # over 5 KB but under 300 lines
        ("cat nested/src/mod.py", False),  # another repo's index
    ],
)
def test_file_read_gate(indexed_repo, command, rewritten) -> None:
    result = decide(command, str(indexed_repo))
    assert (result is not None) is rewritten
    if rewritten:
        assert result.command.startswith("repowise distill --source hook-bash ")


def _run_main(monkeypatch, command: str, cwd: str, session: str = "s-1") -> str:
    payload = {
        "session_id": session,
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "argv", ["repowise-rewrite"])
    with pytest.raises(SystemExit):
        rewrite_hook.main()
    return stdout.getvalue()


def test_identical_command_after_a_rewrite_runs_raw(indexed_repo, monkeypatch) -> None:
    cwd = str(indexed_repo)
    assert _run_main(monkeypatch, "cat src/mod.py", cwd)
    assert _run_main(monkeypatch, "cat src/mod.py", cwd) == ""
    # Only the one repeat: the next read is compacted again.
    assert _run_main(monkeypatch, "cat src/mod.py", cwd)
    # Sessions do not share the rule.
    assert _run_main(monkeypatch, "cat src/mod.py", cwd, session="s-2")


def test_commit_header_after_a_folded_lockfile_survives() -> None:
    raw_lines = ["commit aaa", "    first message"]
    raw_lines += _diff_block("src/app.py", hunks=8, context=5)
    raw_lines += _diff_block("package-lock.json", hunks=40)
    raw_lines += ["commit bbb", "Author: B <b@c>", "    second message"]
    raw_lines += _diff_block("src/other.py", hunks=8, context=5)
    out = GitDiffFilter().distill("\n".join(raw_lines), command="git log -p -2")
    for line in ("commit bbb", "Author: B <b@c>", "    second message", "commit aaa"):
        assert line in out.splitlines()


def test_changed_lines_that_look_like_headers_are_counted() -> None:
    raw_lines = _diff_block("a.sql", hunks=1, context=0)
    raw_lines[-2:] = ["--- removed comment", "+++ added counter"]
    filler = []
    for n in range(10):
        filler += _diff_block(f"b{n}.py", hunks=4, context=4)
    out = GitDiffFilter().distill("\n".join(raw_lines + filler), command="git diff")
    assert "--- removed comment" in out and "+++ added counter" in out


@pytest.mark.parametrize(
    ("command", "output_head", "expected"),
    [
        ("cat src/mod.py", "x = 1\n", "file_read"),
        ("pytest -q", "FAILED test_x.py::test_a\n", "test_output"),
    ],
)
def test_router_keeps_non_git_filters_for_text_that_contains_a_diff(
    command, output_head, expected
) -> None:
    diff = "\n".join(_diff_block("a.py"))
    assert select_filter(command, output_head + diff).name == expected


def test_repeated_git_diff_stays_compact(indexed_repo, monkeypatch) -> None:
    cwd = str(indexed_repo)
    assert _run_main(monkeypatch, "git diff", cwd)
    assert _run_main(monkeypatch, "git diff", cwd)


def test_cat_of_a_file_edited_after_the_index_is_raw(indexed_repo, monkeypatch) -> None:
    import os
    import time

    monkeypatch.chdir(indexed_repo)
    path = indexed_repo / "src" / "mod.py"
    later = time.time() + 60
    os.utime(path, (later, later))
    raw = path.read_text(encoding="utf-8")
    result = distill_output(raw, command="cat src/mod.py")
    assert not result.distilled
