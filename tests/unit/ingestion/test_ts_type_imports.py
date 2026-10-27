"""Tests for TypeScript type-only imports and cycle suppression."""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.analysis.health.refactoring.graph_signals import build_file_scc_index
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file_info(path: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="typescript",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


@pytest.mark.parametrize(
    ("source", "expected_type_only"),
    [
        ('import type { User } from "./user";', True),
        ('import type User from "./user";', True),
        ('import type * as User from "./user";', True),
        ('export type { User } from "./user";', True),
        ('export type * from "./user";', True),
        ('export type * as User from "./user";', True),
        ('import { type User, type Account } from "./user";', True),
        ('export { type User, type Account } from "./user";', True),
        ('import { type User, getUser } from "./user";', False),
        ('export { type User, getUser } from "./user";', False),
        ('import { User, Account } from "./user";', False),
        ('export { User, Account } from "./user";', False),
        ('import User from "./user";', False),
        ('import * as User from "./user";', False),
        ('export * from "./user";', False),
    ],
)
def test_ts_type_only_import_parsing(source: str, expected_type_only: bool) -> None:
    parser = ASTParser()
    parsed = parser.parse_file(_file_info("app.ts"), source.encode("utf-8"))
    assert len(parsed.imports) >= 1
    assert parsed.imports[0].type_only is expected_type_only


def test_ts_mutually_type_only_cycle_suppressed() -> None:
    parser = ASTParser()
    builder = GraphBuilder()

    a_src = 'import type { B } from "./b";\nexport type A = { b: B };\n'
    b_src = 'import type { A } from "./a";\nexport type B = { a: A };\n'

    builder.add_file(parser.parse_file(_file_info("a.ts"), a_src.encode("utf-8")))
    builder.add_file(parser.parse_file(_file_info("b.ts"), b_src.encode("utf-8")))
    graph = builder.build()

    # Graph retains the import edge for reachability
    assert graph.has_edge("a.ts", "b.ts")
    assert graph["a.ts"]["b.ts"]["type_only"] is True
    assert graph.has_edge("b.ts", "a.ts")
    assert graph["b.ts"]["a.ts"]["type_only"] is True

    # Cycle subgraph and SCC index suppress the cycle
    assert builder.cycle_subgraph().number_of_edges() == 0
    assert build_file_scc_index(graph) == {}


def test_ts_mixed_runtime_and_type_cycle_detected() -> None:
    parser = ASTParser()
    builder = GraphBuilder()

    # a -> b is value import, b -> a is value import
    a_src = 'import { bVal } from "./b";\nexport const aVal = bVal + 1;\n'
    b_src = 'import { aVal } from "./a";\nexport const bVal = aVal + 1;\n'

    builder.add_file(parser.parse_file(_file_info("a.ts"), a_src.encode("utf-8")))
    builder.add_file(parser.parse_file(_file_info("b.ts"), b_src.encode("utf-8")))
    graph = builder.build()

    assert builder.cycle_subgraph().number_of_edges() == 2
    scc = build_file_scc_index(graph)
    assert "a.ts" in scc and "b.ts" in scc


def test_ts_parallel_edges_merging() -> None:
    parser = ASTParser()
    builder = GraphBuilder()

    # File a imports b twice: once type-only, once runtime value
    a_src = (
        'import type { BType } from "./b";\n'
        'import { bValue } from "./b";\n'
        'export const a = 1;\n'
    )
    b_src = 'export type BType = number;\nexport const bValue = 2;\n'

    builder.add_file(parser.parse_file(_file_info("a.ts"), a_src.encode("utf-8")))
    builder.add_file(parser.parse_file(_file_info("b.ts"), b_src.encode("utf-8")))
    graph = builder.build()

    # The edge must NOT be type_only because a runtime value is imported
    assert graph["a.ts"]["b.ts"]["type_only"] is False
    assert set(graph["a.ts"]["b.ts"]["imported_names"]) == {"BType", "bValue"}

