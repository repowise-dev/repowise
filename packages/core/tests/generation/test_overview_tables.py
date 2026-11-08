"""Tests for the deterministic tables embedded into the overview."""

from __future__ import annotations

from repowise.core.generation.overview_tables import embed_section


def test_fenced_heading_does_not_end_section() -> None:
    page = (
        "# T\n\n## Parts\n\nold\n\n```bash\n## not a heading\nmake\n```\n\n"
        "after fence\n\n## Next\n\nkeep\n"
    )
    out = embed_section(page, "Parts", "NEW")
    assert "## Parts\n\nNEW\n\n## Next\n\nkeep\n" in out
    assert "not a heading" not in out
    assert "after fence" not in out


def test_unfenced_heading_still_ends_section() -> None:
    page = "# T\n\n## Parts\n\nold\n\n## Next\n\nkeep\n"
    out = embed_section(page, "Parts", "NEW")
    assert out == "# T\n\n## Parts\n\nNEW\n\n## Next\n\nkeep\n"


def test_rule_still_ends_section() -> None:
    page = "## Parts\n\nold\n\n---\n\nfooter\n"
    out = embed_section(page, "Parts", "NEW")
    assert out == "## Parts\n\nNEW\n\n---\n\nfooter\n"


def test_empty_body_removes_section() -> None:
    page = "# T\n\n## Parts\n\nold\n\n## Next\n\nkeep\n"
    out = embed_section(page, "Parts", None)
    assert out == "# T\n\n## Next\n\nkeep\n"


def test_backslashes_in_body_are_literal() -> None:
    page = "## Parts\n\nold\n\n## Next\n"
    out = embed_section(page, "Parts", r"a\1b")
    assert r"a\1b" in out
