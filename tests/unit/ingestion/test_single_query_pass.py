"""The compiled tree-sitter query must execute exactly once per parse_file.

The five extraction passes (symbols, imports, calls, heritage, type refs)
share one materialized capture list; re-running ``cursor.matches()`` per
pass silently multiplies the dominant parse cost by five. This guards the
single-execution contract; per-language output correctness is pinned by
the existing fixture suites.
"""

from __future__ import annotations

from datetime import UTC, datetime

from repowise.core.ingestion import ASTParser
from repowise.core.ingestion.models import FileInfo

_PY_SOURCE = b'''\
import os
from collections import OrderedDict


class Base:
    def greet(self) -> str:
        return "hi"


class Child(Base):
    def greet(self) -> str:
        return helper(os.getcwd())


def helper(arg: str) -> str:
    return arg.upper()
'''


def _file_info(path: str = "pkg/mod.py") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="python",
        size_bytes=len(_PY_SOURCE),
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def test_query_runs_once_and_output_is_complete(monkeypatch):
    import repowise.core.ingestion.parser as parser_mod

    calls = {"n": 0}
    real_run_query = parser_mod._run_query

    def counting_run_query(query, root):
        calls["n"] += 1
        return real_run_query(query, root)

    monkeypatch.setattr(parser_mod, "_run_query", counting_run_query)

    parsed = ASTParser().parse_file(_file_info(), _PY_SOURCE)

    assert calls["n"] == 1, f"compiled query executed {calls['n']} times, expected 1"
    # The single pass must still feed every extraction stage.
    assert {s.name for s in parsed.symbols} >= {"Base", "Child", "helper", "greet"}
    assert {i.module_path for i in parsed.imports} == {"os", "collections"}
    assert any(c.target_name == "helper" for c in parsed.calls)
    assert any(h.parent_name == "Base" for h in parsed.heritage)
