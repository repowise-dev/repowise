"""CSD files produce parsed results even when they contain no definitions."""

import pytest

from repowise.core.ingestion.cobol.csd import parse_csd_file

from .parser._helpers import _make_file_info


@pytest.mark.parametrize(
    "source",
    [
        b"",
        b"\n   \n",
        b"* only a comment\n",
        b"ALTER PROGRAM(P1) GROUP(G)\n",
        b"DEFINE _PGMLIST(STARTUP) GROUP(G)\n",
    ],
    ids=["empty", "whitespace", "comments", "alter-only", "unsupported-resource"],
)
def test_csd_without_definitions_returns_empty_parsed_file(source: bytes) -> None:
    file_info = _make_file_info("region.csd", "csd")

    parsed = parse_csd_file(file_info, source)

    assert parsed.file_info is file_info
    assert parsed.symbols == []
    assert parsed.exports == []
    assert parsed.parse_errors == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (b"DEFINE PROGRAM(P1) GROUP(G)\n", [("P1", 1, 1)]),
        (
            b"* Region\n"
            b"DEFINE PROGRAM(P1) GROUP(G)\n"
            b" DESCRIPTION(FIRST)\n"
            b"* Comment\n"
            b"DEFINE TRANSACTION(T1) GROUP(G)\n"
            b" PROGRAM(P1)\n"
            b"* Trailing comment\n",
            [("P1", 2, 4), ("T1", 5, 7)],
        ),
    ],
    ids=["single-definition", "multiple-definitions"],
)
def test_csd_definition_line_ranges(
    source: bytes, expected: list[tuple[str, int, int]]
) -> None:
    parsed = parse_csd_file(_make_file_info("region.csd", "csd"), source)

    assert [(s.name, s.start_line, s.end_line) for s in parsed.symbols] == expected
    assert parsed.exports == [name for name, _, _ in expected]
    assert parsed.parse_errors == []
