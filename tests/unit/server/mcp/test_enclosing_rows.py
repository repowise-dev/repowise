"""Line and reference rows name the symbol they sit in."""

from __future__ import annotations

import pytest

from repowise.server.mcp_server._edit_sites import _name_enclosing
from repowise.server.mcp_server._enclosing import (
    MODULE_LEVEL,
    _Span,
    display_name,
    mark_enclosing,
)

RANGES = {
    "a.py": [_Span(1, 40, "Store"), _Span(5, 12, "Store.put"), _Span(20, 30, "Store.get")],
    "b.py": [_Span(3, 9, "helper")],
}


def _row(path, line, kind="call", repo=None):
    row = {"path": path, "line": line, "kind": kind, "text": "x"}
    if repo:
        row["repo"] = repo
    return row


def test_a_run_in_one_symbol_names_it_once():
    rows = [_row("a.py", 6), _row("a.py", 8), _row("a.py", 22), _row("b.py", 4)]
    mark_enclosing(rows, {None: RANGES})
    assert [r.get("in") for r in rows] == ["Store.put", None, "Store.get", "helper"]


def test_definitions_are_skipped_and_end_a_run():
    rows = [_row("a.py", 6), _row("a.py", 5, "definition"), _row("a.py", 7)]
    mark_enclosing(rows, {None: RANGES})
    assert [r.get("in") for r in rows] == ["Store.put", None, "Store.put"]


def test_module_level_lines_say_so_only_after_a_symbol():
    rows = [_row("b.py", 1), _row("b.py", 4), _row("b.py", 20)]
    mark_enclosing(rows, {None: RANGES})
    assert [r.get("in") for r in rows] == [None, "helper", MODULE_LEVEL]


def test_each_repo_reads_its_own_index():
    rows = [_row("a.py", 6, repo="web"), _row("a.py", 6, repo="api")]
    mark_enclosing(rows, {"web": RANGES, "api": {"a.py": [_Span(1, 9, "main")]}})
    assert [r["in"] for r in rows] == ["Store.put", "main"]


def test_display_names_drop_paths_and_discriminators():
    assert display_name("src/a.py::Store::put") == "Store.put"
    assert display_name("Validate.java::Validate::notNull#1(String)") == "Validate.notNull"
    assert display_name("lib.rs::f#cfg(not(unix))") == "f"


def test_references_block_groups_lines_by_enclosing_symbol():
    by_file = {"a.py": {"definition": [5], "same_name": [21], "call": [6, 22], "reference": [8, 50]}}
    _name_enclosing(by_file, RANGES)
    # Line 50 is module level and the definition is its own symbol: neither is listed.
    assert by_file["a.py"]["in"] == {"Store.put": [6, 8], "Store.get": [22]}


@pytest.mark.asyncio
async def test_search_lines_name_the_symbol_a_match_sits_in(setup_mcp, tmp_path):
    from repowise.server.mcp_server import search_codebase

    path = tmp_path / "src" / "auth" / "service.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [""] * 70
    lines[24] = "        token_ledger_flush()"
    lines[59] = "        token_ledger_flush()"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    test = tmp_path / "tests" / "test_service.py"
    test.parent.mkdir(parents=True, exist_ok=True)
    test.write_text("def test_flush():\n    token_ledger_flush()\n", encoding="utf-8")

    result = await search_codebase("token_ledger_flush")
    rows = [(r["path"], r["line"], r.get("in")) for r in result.get("lines", [])]
    assert ("src/auth/service.py", 25, "login") in rows
    assert ("src/auth/service.py", 60, "AuthService") in rows
    # A test file's rows keep their plain shape.
    assert ("tests/test_service.py", 2, None) in rows
