"""The vocabulary miner's two contracts.

``extract_terms`` feeds the concept planner, whose binding is measured to be
correct. Its output is therefore pinned byte-for-byte against a fixture
repository: any change to the harvest that moves a single term is a change to
which pages the planner emits, and must be seen rather than discovered later.

``extract_house_terms`` is the richer view — same candidates, plus the sentence
that defines each one and where it was read from. Nothing consumes it yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.generation.concept_tree.vocabulary import (
    extract_house_terms,
    extract_terms,
)

# ---------------------------------------------------------------------------
# The fixture repository
# ---------------------------------------------------------------------------

_README = """\
# Ledger

A ledger for small shops.

## Blast radius

Blast radius is the set of files a change can reach through the import graph.

## Change risk

Change risk scores a diff against the history of the files it touches.

### Your ledger stops guessing

Marketing copy, not a subsystem.

## Getting Started

Boilerplate heading, says nothing about this repository.

## Local paths are masked

A sentence, not a name.

## Headline facts updated 2026-06-22

A dated note.

**Dead code** — code no import path reaches.

**Co-change** is how often two files land in the same commit.
"""

_ARCHITECTURE = """\
# Architecture

## Blast radius

Repeated on purpose: the term is named by two documents.

## Ingestion pipeline

The ingestion pipeline walks the working tree and parses each file.
"""

_DOCS_GUIDE = """\
# Query guide

## Bug magnet

A file that has absorbed an unusual share of the repository's bug fixes.
"""

_DOCS_CHANGELOG = """\
# Changelog

## v2.1.0

Added the audit trail.

## v2.0.1

Fixed the ledger rollover.

## v2.0.0

Renamed the reconciliation engine.

## v1.9.4

Sharpened the diff parser.

## v1.9.3

Rebuilt the index writer.

## v1.9.2

Counted the follow-ups.

## v1.9.1

Polished the report.

## v1.9.0

Introduced the audit trail.
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text(_README, encoding="utf-8")
    (tmp_path / "ARCHITECTURE.md").write_text(_ARCHITECTURE, encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "CHANGELOG.md").write_text(_DOCS_CHANGELOG, encoding="utf-8")
    (docs / "guide.md").write_text(_DOCS_GUIDE, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# The pin
# ---------------------------------------------------------------------------

#: Exactly what ``extract_terms`` returns on the fixture above, captured from
#: the implementation that the concept planner was measured against. This list
#: is not a wish — it is a record. Update it only alongside a deliberate,
#: reviewed change to which concept pages the planner produces, and never to
#: make a failing test pass.
EXPECTED_TERMS = [
    "Ledger",
    "Blast radius",
    "Change risk",
    # A marketing heading. It survives the harvest today, and it stays here:
    # the harvest is the planner's input and the planner discards non-binders
    # anyway. Filtering claims belongs to the ranked view, which renders terms
    # directly and cannot afford them.
    "Your ledger stops guessing",
    "Dead code",
    "Co-change",
    "Architecture",
    "Ingestion pipeline",
    "Query guide",
    "Bug magnet",
]


def test_extract_terms_output_is_byte_identical(repo: Path) -> None:
    """The planner's input does not move."""
    assert extract_terms(repo) == EXPECTED_TERMS


def test_extract_terms_honours_max_terms(repo: Path) -> None:
    assert extract_terms(repo, max_terms=3) == EXPECTED_TERMS[:3]


def test_extract_terms_on_a_repository_with_no_docs(tmp_path: Path) -> None:
    """A repository with nothing to read is a supported outcome, not a failure."""
    assert extract_terms(tmp_path) == []


# ---------------------------------------------------------------------------
# The enriched view
# ---------------------------------------------------------------------------


def test_house_terms_carry_the_same_terms_in_the_same_order(repo: Path) -> None:
    """The enrichment adds fields; it does not add, drop or reorder terms."""
    assert [t.term for t in extract_house_terms(repo)] == EXPECTED_TERMS


def test_a_heading_yields_its_following_sentence_as_the_definition(repo: Path) -> None:
    by_term = {t.term: t for t in extract_house_terms(repo)}
    blast = by_term["Blast radius"]
    assert blast.definition == (
        "Blast radius is the set of files a change can reach through the import graph."
    )
    assert blast.definition_source == "README.md"


def test_a_bolded_lead_in_yields_its_definition(repo: Path) -> None:
    by_term = {t.term: t for t in extract_house_terms(repo)}
    assert by_term["Dead code"].definition == "code no import path reaches."
    assert by_term["Dead code"].definition_source == "README.md"


def test_a_term_named_by_two_documents_records_both(repo: Path) -> None:
    by_term = {t.term: t for t in extract_house_terms(repo)}
    assert by_term["Blast radius"].source_paths == ("README.md", "ARCHITECTURE.md")
    assert by_term["Blast radius"].doc_frequency == 2
    assert by_term["Change risk"].doc_frequency == 1


def test_source_paths_are_repository_relative_and_posix(repo: Path) -> None:
    by_term = {t.term: t for t in extract_house_terms(repo)}
    assert by_term["Bug magnet"].source_paths == ("docs/guide.md",)


def test_the_definition_scan_stops_at_the_next_heading(repo: Path) -> None:
    """A section with no prose of its own has no definition.

    Without a stop the scan runs on into the following section and hands back
    a sentence about a different subject — a confident, wrong definition,
    which is worse than none.
    """
    by_term = {t.term: t for t in extract_house_terms(repo)}
    assert by_term["Architecture"].definition is None
    assert by_term["Architecture"].definition_source is None
    assert by_term["Query guide"].definition is None


def test_a_term_the_codebase_defines_is_marked_as_a_symbol(repo: Path) -> None:
    """Only a real symbol may be rendered in backticks.

    Grounding strips the backticks off a symbol-shaped token it cannot
    resolve, so a coined term dressed as code is demoted mid-page — silently,
    which is the failure shape this repository has shipped before.
    """
    by_term = {
        t.term: t for t in extract_house_terms(repo, known_symbols={"Blast radius", "Ledger"})
    }
    assert by_term["Blast radius"].is_indexed_symbol is True
    assert by_term["Bug magnet"].is_indexed_symbol is False


def test_without_a_symbol_set_nothing_claims_to_be_one(repo: Path) -> None:
    assert all(not t.is_indexed_symbol for t in extract_house_terms(repo))


def test_house_terms_on_a_repository_with_no_docs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Finding nothing is reported, not returned as if it were a finding."""
    with caplog.at_level("WARNING"):
        assert extract_house_terms(tmp_path) == []
    assert any("no house terms found" in r.message for r in caplog.records)
