"""Fixed-format masking: compiler-option cards starting in column 1 are skipped (#4475)."""

from repowise.core.ingestion.cobol.reference_format import normalize_fixed_format

PROGRAM = """CBL APOST
       IDENTIFICATION DIVISION.
       PROGRAM-ID. DEMO.
       PROCEDURE DIVISION.
           STOP RUN.
"""


def test_a_column_one_compiler_directive_is_blanked():
    result = normalize_fixed_format(PROGRAM)

    lines = result.split("\n")
    assert lines[0] == ""
    assert "CBL" not in result
    assert "IDENTIFICATION DIVISION." in lines[1]
    assert len(lines) == len(PROGRAM.split("\n"))


def test_a_column_one_process_directive_is_blanked():
    result = normalize_fixed_format("PROCESS SQL\n       IDENTIFICATION DIVISION.\n")

    assert "PROCESS" not in result
    assert "IDENTIFICATION DIVISION." in result


def test_a_column_one_data_name_that_starts_like_a_directive_is_kept():
    """A copybook's first code line can be a data name such as CBL-REC."""
    source = "CBL-REC       PIC X(8).\n"
    result = normalize_fixed_format(source)

    # Columns 1-7 are the sequence area and are blanked, but the entry stays.
    assert "PIC X(8)." in result
    assert result.strip() != ""
