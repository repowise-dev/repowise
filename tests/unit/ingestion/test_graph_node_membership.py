"""SCC + symbol-community membership snapshot (graph_node_membership).

Proves ``node_membership_snapshot`` materializes file-level import cycles
(strongly-connected components of size >= 2) as rows the refactoring / web
layers can read without rebuilding the graph, and that acyclic files are
absent.
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo, Import, ParsedFile


def _fi(path: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="python",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _imp(module_path: str) -> Import:
    return Import(
        raw_statement=f"import {module_path}",
        module_path=module_path,
        imported_names=[],
        is_relative=False,
        resolved_file=None,
    )


def _parsed(path: str, imports: list[Import] | None = None) -> ParsedFile:
    return ParsedFile(
        file_info=_fi(path),
        symbols=[],
        imports=imports or [],
        exports=[],
        docstring=None,
        parse_errors=[],
        content_hash="",
    )


def test_cycle_files_are_materialized_as_scc_rows():
    b = GraphBuilder()
    # a <-> b mutual import (a 2-file cycle); c is acyclic.
    b.add_file(_parsed("a.py", [_imp("b")]))
    b.add_file(_parsed("b.py", [_imp("a")]))
    b.add_file(_parsed("c.py", [_imp("a")]))
    b.build()

    snap = b.node_membership_snapshot()

    assert "a.py" in snap and "b.py" in snap
    assert snap["a.py"]["scc_size"] == 2
    assert snap["b.py"]["scc_size"] == 2
    assert snap["a.py"]["scc_id"] == snap["b.py"]["scc_id"]
    assert snap["a.py"]["node_type"] == "file"
    # c.py is not in a cycle → no SCC row.
    assert "c.py" not in snap or snap["c.py"].get("scc_id") is None


def test_acyclic_graph_has_no_scc_rows():
    b = GraphBuilder()
    b.add_file(_parsed("a.py", [_imp("b")]))
    b.add_file(_parsed("b.py"))
    b.build()
    snap = b.node_membership_snapshot()
    assert all(v.get("scc_id") is None for v in snap.values())
