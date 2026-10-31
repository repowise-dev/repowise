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

    def test_level_88_condition_names_are_children_of_their_item(
        self, parser: ASTParser
    ) -> None:
        source = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. FLAGS.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-GROUP.
          05 WS-STATUS PIC X.
             88 STATUS-OK VALUE 'Y'.
      * a comment between conditions
             88 STATUS-BAD VALUE 'N'.
          05 WS-OTHER PIC X.
       01 WS-RETURN-FLAG PIC X.
          88 APPL-AOK VALUE '0'.
       01 FILLER PIC X.
          88 ORPHAN-FLAG VALUE 'Z'.
       PROCEDURE DIVISION.
       MAIN-PARA.
           STOP RUN.
"""
        result = parser.parse_file(_cobol("flags.cbl"), source)
        by_name = {s.name: s for s in result.symbols}

        assert by_name["STATUS-OK"].parent_name == "WS-STATUS"
        assert by_name["STATUS-BAD"].parent_name == "WS-STATUS"
        assert by_name["APPL-AOK"].parent_name == "WS-RETURN-FLAG"
        assert by_name["APPL-AOK"].id == "flags.cbl::WS-RETURN-FLAG::APPL-AOK"
        assert by_name["APPL-AOK"].kind == "variable"
        # A FILLER item has no name to own its conditions.
        assert by_name["ORPHAN-FLAG"].parent_name is None
        # Other levels keep their top-level ids.
        for name in ("WS-GROUP", "WS-STATUS", "WS-OTHER", "WS-RETURN-FLAG", "MAIN-PARA"):
            assert by_name[name].parent_name is None
            assert by_name[name].id == f"flags.cbl::{name}"

    @staticmethod
    def _parse_with_bloat_threshold(
        parser: ASTParser, monkeypatch: pytest.MonkeyPatch, entries: str
    ) -> tuple[list, list[dict]]:
        import repowise.core.ingestion.parser as parser_module

        source = (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. BLOAT.\n"
            "       DATA DIVISION.\n"
            "       WORKING-STORAGE SECTION.\n" + entries
        ).encode()
        warnings: list[dict] = []
        monkeypatch.setattr(parser_module, "_SYMBOL_COUNT_WARN_THRESHOLD", 5)
        monkeypatch.setattr(
            parser_module.log, "warning", lambda event, **kw: warnings.append(kw)
        )
        return parser.parse_file(_cobol("bloat.cbl"), source).symbols, warnings

    def test_level_88_children_do_not_count_toward_symbol_bloat(
        self, parser: ASTParser, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conditions = "".join(
            f"          88 COND-{i:03d} VALUE '{i % 10}'.\n" for i in range(12)
        )
        symbols, warnings = self._parse_with_bloat_threshold(
            parser, monkeypatch, "       01 WS-CODE PIC X.\n" + conditions
        )

        assert len(symbols) == 14
        assert warnings == []

    def test_other_cobol_data_items_still_count_toward_symbol_bloat(
        self, parser: ASTParser, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        items = "".join(f"       01 WS-ITEM-{i:03d} PIC X.\n" for i in range(12))
        symbols, warnings = self._parse_with_bloat_threshold(parser, monkeypatch, items)

        assert len(symbols) == 13
        assert [w["symbol_count"] for w in warnings] == [13]

    def test_same_condition_under_same_named_items(self, parser: ASTParser) -> None:
        source = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. DUP.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 GRP-A.
          05 WS-FLAG PIC X.
             88 IS-ON VALUE 'Y'.
       01 GRP-B.
          05 WS-FLAG PIC X.
             88 IS-ON VALUE 'Y'.
"""
        result = parser.parse_file(_cobol("dup.cbl"), source)
        conditions = [s for s in result.symbols if s.name == "IS-ON"]

        # COBOL ids carry no discriminator, so the store keeps the last twin.
        assert [s.id for s in conditions] == ["dup.cbl::WS-FLAG::IS-ON"] * 2
        assert [s.parent_name for s in conditions] == ["WS-FLAG", "WS-FLAG"]

    def test_condition_after_redefines_and_occurs(self, parser: ASTParser) -> None:
        source = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. SHAPES.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-NUM PIC 9(4).
       01 WS-NUM-X REDEFINES WS-NUM PIC X(4).
          88 NUM-BLANK VALUE SPACES.
       01 WS-TABLE.
          05 WS-ENTRY PIC X OCCURS 10 TIMES.
             88 ENTRY-SET VALUE 'Y'.
"""
        result = parser.parse_file(_cobol("shapes.cbl"), source)
        by_name = {s.name: s for s in result.symbols}

        assert by_name["NUM-BLANK"].parent_name == "WS-NUM-X"
        assert by_name["ENTRY-SET"].parent_name == "WS-ENTRY"

    def test_condition_after_copy_or_section_start_stays_top_level(
        self, parser: ASTParser
    ) -> None:
        source = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. EDGES.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
          88 FIRST-COND VALUE 'A'.
       01 WS-A PIC X.
       COPY CPYA.
          88 AFTER-COPY VALUE 'B'.
       LINKAGE SECTION.
          88 LINK-COND VALUE 'C'.
       01 LK-A PIC X.
"""
        result = parser.parse_file(_cobol("edges.cbl"), source)
        by_name = {s.name: s for s in result.symbols}

        for name in ("FIRST-COND", "AFTER-COPY", "LINK-COND"):
            assert by_name[name].parent_name is None
            assert by_name[name].id == f"edges.cbl::{name}"

    def test_conditions_in_a_two_program_file(self, parser: ASTParser) -> None:
        source = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. ONE.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-ONE PIC X.
          88 ONE-ON VALUE 'Y'.
       PROCEDURE DIVISION.
       P1.
           GOBACK.
       END PROGRAM ONE.
       IDENTIFICATION DIVISION.
       PROGRAM-ID. TWO.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-TWO PIC X.
          88 TWO-ON VALUE 'Y'.
       PROCEDURE DIVISION.
       P2.
           GOBACK.
       END PROGRAM TWO.
"""
        result = parser.parse_file(_cobol("two.cbl"), source)
        by_name = {s.name: s for s in result.symbols}

        assert {"ONE", "TWO"} <= {s.name for s in result.symbols if s.kind == "module"}
        assert by_name["ONE-ON"].id == "two.cbl::WS-ONE::ONE-ON"
        assert by_name["TWO-ON"].id == "two.cbl::WS-TWO::TWO-ON"

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


class TestCobolCallScope:
    """A PERFORM stays in its program and its copybooks; a CALL names a PROGRAM-ID."""

    @staticmethod
    def _resolve(parser: ASTParser, files: dict[str, bytes], imports: dict[str, set[str]]):
        parsed = {path: parser.parse_file(_cobol(path), source) for path, source in files.items()}
        resolver = CallResolver(parsed, {path: imports.get(path, set()) for path in parsed})
        caller = next(iter(files))
        return {(e.callee_id, e.origin) for e in resolver.resolve_file(caller, parsed[caller].calls)}

    def test_a_perform_never_binds_to_another_programs_paragraph(self, parser: ASTParser) -> None:
        caller = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. A.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM 1000-EXIT.
"""
        other = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. B.
       PROCEDURE DIVISION.
       1000-EXIT.
           EXIT.
"""
        assert self._resolve(parser, {"a.cbl": caller, "b.cbl": other}, {}) == set()

    def test_a_perform_reaches_a_paragraph_its_copybook_brings_in(self, parser: ASTParser) -> None:
        caller = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. A.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM STORE-PFKEY THRU STORE-PFKEY-EXIT.
       COPY CSSTRPFY.
"""
        copybook = b"""      * COMMON CODE
      *
       STORE-PFKEY.
           CONTINUE.
       STORE-PFKEY-EXIT.
           EXIT.
"""
        edges = self._resolve(
            parser, {"a.cbl": caller, "CSSTRPFY.cpy": copybook}, {"a.cbl": {"CSSTRPFY.cpy"}}
        )
        # The THRU end is part of the range, not a second target.
        assert edges == {("CSSTRPFY.cpy::STORE-PFKEY", "import_scoped")}

    def test_a_call_binds_to_the_program_not_a_same_named_data_item(
        self, parser: ASTParser
    ) -> None:
        caller = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. LGACDB01.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       77 LGACDB02 PIC X(8) VALUE 'LGACDB02'.
       PROCEDURE DIVISION.
       MAIN-PARA.
           EXEC CICS LINK PROGRAM(LGACDB02) END-EXEC.
           CALL 'LGACDB02'.
"""
        callee = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. LGACDB02.
       PROCEDURE DIVISION.
       MAIN-PARA.
           GOBACK.
"""
        edges = self._resolve(parser, {"lgacdb01.cbl": caller, "lgacdb02.cbl": callee}, {})
        assert edges == {("lgacdb02.cbl::LGACDB02", "global_unique")}

    def test_a_call_skips_a_same_named_paragraph(self, parser: ASTParser) -> None:
        caller = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. A.
       PROCEDURE DIVISION.
       MAIN-PARA.
           CALL 'B'.
       B.
           EXIT.
"""
        callee = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. B.
       PROCEDURE DIVISION.
       MAIN-PARA.
           GOBACK.
"""
        edges = self._resolve(parser, {"a.cbl": caller, "b.cbl": callee}, {})
        assert edges == {("b.cbl::B", "global_unique")}

    def test_an_unresolved_perform_never_binds_to_a_program(self, parser: ASTParser) -> None:
        caller = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. A.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM B.
"""
        callee = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. B.
       PROCEDURE DIVISION.
       MAIN-PARA.
           GOBACK.
"""
        assert self._resolve(parser, {"a.cbl": caller, "b.cbl": callee}, {}) == set()

    def test_a_program_naming_itself_is_not_a_transfer(self, parser: ASTParser) -> None:
        caller = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. COCRDLIC.
       PROCEDURE DIVISION.
       MAIN-PARA.
           EXEC CICS XCTL PROGRAM('COCRDLIC') END-EXEC.
"""
        assert self._resolve(parser, {"cocrdlic.cbl": caller}, {}) == set()


class TestCobolFreeFormat:
    def test_free_format_program_parses_symbols_and_calls(self, parser: ASTParser) -> None:
        source = (
            b">>SOURCE FORMAT FREE\n"
            b"IDENTIFICATION DIVISION.\n"
            b"PROGRAM-ID. HELLO.\n"
            b"DATA DIVISION.\n"
            b"WORKING-STORAGE SECTION.\n"
            b"01 WS-A PIC 9. *> a counter\n"
            b"PROCEDURE DIVISION.\n"
            b"MAIN-PARA.\n"
            b"    PERFORM SHOW-PARA\n"
            b"    STOP RUN.\n"
            b"SHOW-PARA.\n"
            b"    DISPLAY \"HI\". *> greet\n"
        )
        parsed = parser.parse_file(_cobol("HELLO.cbl"), source)
        assert parsed.parse_errors == []
        assert {s.name: s.kind for s in parsed.symbols} == {
            "HELLO": "module",
            "WS-A": "variable",
            "MAIN-PARA": "function",
            "SHOW-PARA": "function",
        }
        assert [c.target_name for c in parsed.calls] == ["SHOW-PARA"]

