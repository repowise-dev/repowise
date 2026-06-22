"""Unit tests for the Keep-a-Changelog parser."""

from __future__ import annotations

from repowise.core.upgrade.changelog import (
    entries_between,
    parse_changelog,
)

SAMPLE = """# Changelog

Some preamble that should be ignored.

<!-- a comment -->

---

## [0.21.0] - 2026-06-19

### Added
- First feature. (#1)
- Second feature.

### Fixed
- A bug fix.

---

## [0.20.0] - 2026-06-16

### Changed
- A change.

## [0.19.1] — Unreleased

### Added
- Pending thing.
"""


def test_parses_versions_in_order():
    entries = parse_changelog(SAMPLE)
    assert [e.version for e in entries] == ["0.21.0", "0.20.0", "0.19.1"]


def test_parses_sections_and_items():
    entries = parse_changelog(SAMPLE)
    top = entries[0]
    assert [s.name for s in top.sections] == ["Added", "Fixed"]
    assert top.sections[0].items == ("First feature. (#1)", "Second feature.")
    assert top.sections[1].items == ("A bug fix.",)


def test_captures_label_with_any_dash():
    entries = parse_changelog(SAMPLE)
    assert entries[0].label == "2026-06-19"
    assert entries[2].label == "Unreleased"  # em dash header still parses


def test_release_tuple():
    entries = parse_changelog(SAMPLE)
    assert entries[0].release_tuple == (0, 21, 0)


def test_preamble_bullets_are_not_attributed_to_a_release():
    text = "# Changelog\n- stray bullet\n\n## [1.0.0] - x\n### Added\n- real\n"
    entries = parse_changelog(text)
    assert len(entries) == 1
    assert entries[0].sections[0].items == ("real",)


def test_empty_input():
    assert parse_changelog("") == []


def test_trailing_dash_header_keeps_release_with_null_label():
    """A malformed ``## [x] -`` header degrades to label=None, not a dropped release."""
    text = "## [1.2.0] - \n### Added\n- kept.\n"
    entries = parse_changelog(text)
    assert len(entries) == 1
    assert entries[0].version == "1.2.0"
    assert entries[0].label is None
    assert entries[0].sections[0].items == ("kept.",)


def test_no_label_header_parses():
    text = "## [1.2.0]\n### Added\n- kept.\n"
    entries = parse_changelog(text)
    assert len(entries) == 1
    assert entries[0].label is None


def test_two_part_version():
    text = "## [1.2] - 2026-01-01\n### Added\n- x.\n"
    entries = parse_changelog(text)
    assert entries[0].version == "1.2"
    assert entries[0].release_tuple == (1, 2)


def test_entries_between_exclusive_lower_inclusive_upper():
    entries = parse_changelog(SAMPLE)
    sel = entries_between(entries, newer_than="0.20.0", up_to="0.21.0")
    assert [e.version for e in sel] == ["0.21.0"]


def test_entries_between_open_lower_bound():
    entries = parse_changelog(SAMPLE)
    sel = entries_between(entries, newer_than=None, up_to="0.20.0")
    assert [e.version for e in sel] == ["0.20.0", "0.19.1"]


def test_entries_between_open_upper_bound():
    entries = parse_changelog(SAMPLE)
    sel = entries_between(entries, newer_than="0.19.1")
    assert [e.version for e in sel] == ["0.21.0", "0.20.0"]


def test_entries_between_same_version_is_empty():
    entries = parse_changelog(SAMPLE)
    assert entries_between(entries, newer_than="0.21.0") == []
