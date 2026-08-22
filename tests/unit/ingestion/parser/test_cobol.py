"""COBOL 85 parsing and static call-graph coverage."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.parser import ASTParser

from ._helpers import _make_file_info

grammar_pack = pytest.importorskip(
    "tree_sitter_language_pack", reason="run `uv sync --all-packages`"
)
try:
    grammar_pack.get_language("cobol")
except Exception as exc:  # pragma: no cover - depends on first-use grammar cache/network
    pytest.skip(f"COBOL grammar unavailable: {exc}", allow_module_level=True)


MAIN_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. main-app.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 CUSTOMER-RECORD.
          05 CUSTOMER-NAME PIC X(30).
       PROCEDURE DIVISION.
       main-section SECTION.
       main-paragraph.
           CALL 'worker' USING CUSTOMER-RECORD.
           PERFORM cleanup.
       cleanup.
           STOP RUN.
"""

WORKER_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. WORKER.
       PROCEDURE DIVISION.
       ENTRY-PARAGRAPH.
           GOBACK.
"""


def _cobol(path: str):
    return _make_file_info(path, "cobol")


class TestCobolRegistration:
    def test_extensions_and_shared_grammar_loader(self) -> None:
        spec = REGISTRY.get("cobol")
        assert spec is not None
        assert {REGISTRY.from_extension(ext) for ext in (".cbl", ".cob", ".cobol", ".cpy")} == {
            "cobol"
        }
        assert spec.grammar_package == "tree_sitter_language_pack"
        assert spec.grammar_loader == "get_language"
        assert spec.grammar_loader_args == ("cobol",)


class TestCobolSymbols:
    def test_program_procedures_and_data_items(self, parser: ASTParser) -> None:
        result = parser.parse_file(_cobol("main.cbl"), MAIN_SOURCE)
        symbols = {(symbol.name, symbol.kind): symbol for symbol in result.symbols}

        assert ("MAIN-APP", "module") in symbols
        assert ("MAIN-SECTION", "function") in symbols
        assert ("MAIN-PARAGRAPH", "function") in symbols
        assert ("CLEANUP", "function") in symbols
        assert ("CUSTOMER-RECORD", "variable") in symbols
        assert ("CUSTOMER-NAME", "variable") in symbols
        assert not result.parse_errors

    def test_program_id_without_terminal_period_recovers_name(self, parser: ASTParser) -> None:
        source = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. COURSE01
       AUTHOR. EXAMPLE.
       PROCEDURE DIVISION.
       MAIN-PARAGRAPH.
           STOP RUN.
"""
        result = parser.parse_file(_cobol("course.cobol"), source)
        modules = [symbol for symbol in result.symbols if symbol.kind == "module"]
        assert [symbol.name for symbol in modules] == ["COURSE01"]

    def test_procedure_ranges_attribute_calls_to_the_innermost_paragraph(
        self, parser: ASTParser
    ) -> None:
        result = parser.parse_file(_cobol("main.cbl"), MAIN_SOURCE)
        paragraph = next(symbol for symbol in result.symbols if symbol.name == "MAIN-PARAGRAPH")
        calls = {call.target_name: call for call in result.calls}

        assert calls["WORKER"].caller_symbol_id == paragraph.id
        assert calls["CLEANUP"].caller_symbol_id == paragraph.id


class TestCobolCalls:
    def test_static_call_and_perform_resolve(self, parser: ASTParser) -> None:
        main = parser.parse_file(_cobol("main.cbl"), MAIN_SOURCE)
        worker = parser.parse_file(_cobol("worker.cbl"), WORKER_SOURCE)
        parsed = {"main.cbl": main, "worker.cbl": worker}
        resolver = CallResolver(parsed, {path: set() for path in parsed})

        resolved = resolver.resolve_file("main.cbl", main.calls)
        targets = {edge.callee_id: edge.origin for edge in resolved}

        assert targets["worker.cbl::WORKER"] == "global_unique"
        assert targets["main.cbl::CLEANUP"] == "same_file"

    def test_dynamic_call_target_is_deliberately_silent(self, parser: ASTParser) -> None:
        source = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. DYNAMIC-CALLER.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 PROGRAM-NAME PIC X(8).
       PROCEDURE DIVISION.
       MAIN-PARAGRAPH.
           CALL PROGRAM-NAME.
           STOP RUN.
"""
        result = parser.parse_file(_cobol("dynamic.cbl"), source)
        assert result.calls == []
