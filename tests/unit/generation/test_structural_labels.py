"""Tests for deterministic structural-page localization labels."""

from repowise.core.generation.structural_labels import (
    ENGLISH_LABELS,
    GERMAN_LABELS,
    resolve_structural_labels,
)


def test_resolves_german_labels() -> None:
    assert resolve_structural_labels("de")["overview"] == "Überblick"


def test_unknown_language_uses_complete_english_catalog() -> None:
    assert resolve_structural_labels("xx") == ENGLISH_LABELS
    assert resolve_structural_labels(None) == ENGLISH_LABELS


def test_catalogs_cover_file_page_fragments_and_scc_table_labels() -> None:
    required_labels = {
        "is_a",
        "entry_point",
        "test",
        "source_file",
        "exposes",
        "public_symbol",
        "depends_on_other_files",
        "other_file",
        "imported_by",
    }

    assert required_labels <= ENGLISH_LABELS.keys()
    assert ENGLISH_LABELS["imported_by"] == "Imported by"
    assert GERMAN_LABELS["de"].keys() == ENGLISH_LABELS.keys()
