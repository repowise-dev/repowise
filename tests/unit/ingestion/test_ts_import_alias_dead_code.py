"""Regression tests: TS/JS aliased imports keep the imported symbol live.

``import { beta as renamedBeta } from "./lib"`` used to stamp the *local*
alias on the imports edge, so the unused-export pass — which asks whether the
*source* module's symbol name appears in ``imported_names`` — never matched
and reported ``beta`` as ``safe_to_delete``. Aliasing is mandatory whenever
two modules export the same name, so registries of same-named symbols got the
worst of it. These tests drive the real parser → GraphBuilder →
DeadCodeAnalyzer and assert the aliased symbol is no longer flagged.
"""

from __future__ import annotations

from datetime import datetime

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


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


def _graph_from_sources(sources: dict[str, str]) -> nx.DiGraph:
    builder = GraphBuilder()
    for path, src in sources.items():
        parsed = _PARSER.parse_file(_file_info(path), src.encode("utf-8"))
        builder.add_file(parsed)
    return builder.build()


def _unused_export_names(graph: nx.DiGraph) -> set[str]:
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
    report = analyzer.analyze({"detect_unreachable_files": False, "detect_zombie_packages": False})
    return {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}


def _imported_names(src: str, path: str = "pkg/m.ts") -> list[str]:
    """Parse a single-statement TS source and return the edge's imported names."""
    parsed = _PARSER.parse_file(_file_info(path), src.encode("utf-8"))
    assert parsed.imports, f"no import captured for: {src!r}"
    return parsed.imports[0].imported_names


def _bindings(src: str, path: str = "pkg/m.ts") -> list[tuple[str, str | None]]:
    parsed = _PARSER.parse_file(_file_info(path), src.encode("utf-8"))
    assert parsed.imports, f"no import captured for: {src!r}"
    return [(b.local_name, b.exported_name) for b in parsed.imports[0].bindings]


def _local_names(src: str, path: str = "pkg/m.ts") -> list[str]:
    parsed = _PARSER.parse_file(_file_info(path), src.encode("utf-8"))
    assert parsed.imports, f"no import captured for: {src!r}"
    return parsed.imports[0].local_names


# ---------------------------------------------------------------------------
# Binding extraction — import statements
# ---------------------------------------------------------------------------


class TestImportAliasBindings:
    def test_plain_named_import_records_source_name(self) -> None:
        assert _imported_names('import { alpha } from "./lib";') == ["alpha"]

    def test_aliased_named_import_records_source_name(self) -> None:
        # `beta as renamedBeta` imports the source module's `beta`;
        # reachability is about beta.
        assert _imported_names('import { beta as renamedBeta } from "./lib";') == ["beta"]

    def test_aliased_named_import_keeps_local_name_on_binding(self) -> None:
        # Call resolution still needs the local alias to resolve `renamedBeta(...)`.
        assert _bindings('import { beta as renamedBeta } from "./lib";') == [
            ("renamedBeta", "beta")
        ]

    def test_aliased_require_destructure_records_source_name(self) -> None:
        assert _imported_names('const { beta: renamedBeta } = require("./lib");') == ["beta"]

    def test_aliased_require_destructure_keeps_local_name_on_binding(self) -> None:
        assert _bindings('const { beta: renamedBeta } = require("./lib");') == [
            ("renamedBeta", "beta")
        ]

    def test_default_import_still_records_local_name(self) -> None:
        # The source name is literally `default`; the local name is the only
        # useful one, and the namespace rescue matches it against the file stem.
        assert _imported_names('import React from "./react";') == ["React"]

    def test_namespace_import_still_records_local_name(self) -> None:
        assert _imported_names('import * as utils from "./utils";') == ["utils"]


class TestLocalNames:
    """``Import.local_names`` is what a caller reads to recognise an identifier
    as it appears in the importing file — a router DSL naming a handler, or the
    external-binding exclusion in call resolution."""

    def test_alias_local_name_is_the_alias(self) -> None:
        src = 'import { beta as renamedBeta } from "./lib";'
        assert _local_names(src) == ["renamedBeta"]
        assert _imported_names(src) == ["beta"]

    def test_plain_import_local_name_matches_the_source_name(self) -> None:
        assert _local_names('import { alpha } from "./lib";') == ["alpha"]

    def test_require_destructure_alias_local_name_is_the_alias(self) -> None:
        assert _local_names('const { beta: renamedBeta } = require("./lib");') == ["renamedBeta"]


# ---------------------------------------------------------------------------
# End-to-end: parser → graph → dead-code analyzer
# ---------------------------------------------------------------------------


_LIB = """
export function alpha() { return 1 }
export function beta() { return 2 }
"""


class TestAliasedImportNotDeadCode:
    def test_value_position_aliased_import_is_not_unused(self) -> None:
        # Value-position references only: no call edge exists to rescue the
        # symbol, so the imports edge is the sole usage signal.
        graph = _graph_from_sources(
            {
                "lib.ts": _LIB,
                "registry.ts": """
import { alpha } from "./lib"
import { beta as renamedBeta } from "./lib"

export const registry = [alpha, renamedBeta]
""",
            }
        )
        flagged = _unused_export_names(graph)
        assert "beta" not in flagged
        assert "alpha" not in flagged

    def test_aliased_require_destructure_is_not_unused(self) -> None:
        graph = _graph_from_sources(
            {
                "lib.ts": _LIB,
                "registry.ts": """
const { beta: renamedBeta } = require("./lib")

module.exports = { registry: [renamedBeta] }
""",
            }
        )
        assert "beta" not in _unused_export_names(graph)

    def test_genuinely_unimported_symbol_is_still_flagged(self) -> None:
        # The alias fix must not blanket-rescue everything in the target file.
        graph = _graph_from_sources(
            {
                "lib.ts": _LIB,
                "registry.ts": """
import { beta as renamedBeta } from "./lib"

export const registry = [renamedBeta]
""",
            }
        )
        assert "alpha" in _unused_export_names(graph)
