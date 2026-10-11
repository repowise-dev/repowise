"""Unit tests for the symbol-reference hallucination validator."""

from __future__ import annotations

from repowise.core.generation.page_generator.validation import _validate_symbol_references
from repowise.core.ingestion.models import ParsedFile

from .conftest import _make_file_info, _make_symbol


def _parsed() -> ParsedFile:
    return ParsedFile(
        file_info=_make_file_info(path="pkg/mod.py"),
        symbols=[_make_symbol(name="Thing", file_path="pkg/mod.py")],
        imports=[],
        exports=["Thing"],
        docstring="mod",
        parse_errors=[],
        content_hash="h",
    )


def test_flags_unknown_single_name() -> None:
    warns = _validate_symbol_references("Uses `PhantomThing` heavily.", _parsed())
    assert warns == ["PhantomThing"]


def test_known_symbol_and_export_pass() -> None:
    assert _validate_symbol_references("`Thing` is exported.", _parsed()) == []


def test_dotted_member_access_is_never_flagged() -> None:
    """Attribute chains can't be verified from the symbol table (dataclass
    fields and ORM columns are not symbols), so dotted refs are skipped even
    when no segment is known."""
    content = "`Thing.updated_at` and `config.coverage_pct` and `Fake.member` too."
    assert _validate_symbol_references(content, _parsed()) == []


def test_paths_commands_and_short_refs_pass() -> None:
    content = "See `pkg/mod.py`, run `repowise-update`, index `db` by `x`."
    assert _validate_symbol_references(content, _parsed()) == []
