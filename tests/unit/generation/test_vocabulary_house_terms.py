"""The vocabulary miner's two contracts.

``extract_terms`` feeds the concept planner, whose binding is measured to be
correct. Its output is therefore pinned byte-for-byte against a fixture
repository: any change to the harvest that moves a single term is a change to
which pages the planner emits, and must be seen rather than discovered later.

``extract_house_terms`` is the richer view — same candidates, plus the sentence
that defines each one and where it was read from. Nothing consumes it yet.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from repowise.core.generation.concept_tree.vocabulary import (
    _doc_paths,
    _scan_tree,
    extract_house_terms,
    extract_terms,
)

#: Named rather than left to the root logger: a suite-wide run may have raised
#: the level on an ancestor, and the assertion is about this module reporting
#: its own failures, not about global logging configuration.
_LOGGER = "repowise.core.generation.concept_tree.vocabulary"

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

## 2 · Reconciliation: matching two ledgers

Reconciliation pairs a bank line against a book line.

## Hotspots

## Response
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

## Audit trail

The audit trail records every posting.
"""


# Source prose. A term has to be spelled by the code as well as by the docs,
# so the fixture is a repository rather than a documents folder. Half of it is
# TypeScript on purpose: a miner that reads only Python scores a TypeScript
# repository zero and then rejects its whole vocabulary.

_SRC_ANALYSIS = '''\
"""Blast radius for the ledger.

Blast radius is every file a change can reach by following imports.
"""


def walk():
    """Change risk is the history of the files a diff touches."""
'''

_SRC_HISTORY = '''\
"""Co-change table.

Co-change counts how often two files land in the same commit.
"""
'''

_SRC_HOTSPOTS = '''\
"""Churn ranking for hotspots."""
'''

_SRC_RESPONSE = '''\
"""Response helpers.

Response objects are built here. Response shaping happens later.
"""
'''

_SRC_RECONCILE = '''\
"""Reconciliation of bank lines against book lines."""
'''

_WEB_GRAPH = """\
/**
 * Bug magnet detection for the graph view.
 *
 * A bug magnet is a file that has absorbed far more fixes than its neighbours.
 */
export function bugMagnets() {}
"""

_WEB_DEAD = """\
/**
 * Dead code overlay.
 */
export function deadCode() {}
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    # The repository is a named directory, not tmp_path itself: its own name
    # is one of the things the miner has to exclude.
    root = tmp_path / "ledger"
    root.mkdir()
    (root / "README.md").write_text(_README, encoding="utf-8")
    (root / "ARCHITECTURE.md").write_text(_ARCHITECTURE, encoding="utf-8")
    docs = root / "docs"
    docs.mkdir()
    (docs / "CHANGELOG.md").write_text(_DOCS_CHANGELOG, encoding="utf-8")
    (docs / "guide.md").write_text(_DOCS_GUIDE, encoding="utf-8")

    src = root / "src"
    src.mkdir()
    (src / "analysis.py").write_text(_SRC_ANALYSIS, encoding="utf-8")
    (src / "history.py").write_text(_SRC_HISTORY, encoding="utf-8")
    (src / "http_helpers.py").write_text(_SRC_RESPONSE, encoding="utf-8")
    reconciliation = src / "reconciliation"
    reconciliation.mkdir()
    (reconciliation / "engine.py").write_text(_SRC_RECONCILE, encoding="utf-8")
    hotspots = src / "hotspots"
    hotspots.mkdir()
    (hotspots / "rank.py").write_text(_SRC_HOTSPOTS, encoding="utf-8")

    web = root / "web"
    web.mkdir()
    (web / "graph.ts").write_text(_WEB_GRAPH, encoding="utf-8")
    (web / "dead.tsx").write_text(_WEB_DEAD, encoding="utf-8")

    # Vendored code is somebody else's vocabulary and must not be counted.
    vendored = root / "node_modules" / "other"
    vendored.mkdir(parents=True)
    (vendored / "index.js").write_text(
        "/** Ingestion pipeline for a different project. */", encoding="utf-8"
    )
    return root


# ---------------------------------------------------------------------------
# The pin
# ---------------------------------------------------------------------------

#: Exactly what ``extract_terms`` returns on the fixture above, captured from
#: the implementation that the concept planner was measured against. This list
#: is not a wish — it is a record. Update it only alongside a deliberate,
#: reviewed change to which concept pages the planner produces, and never to
#: make a failing test pass.
EXPECTED_TERMS = [
    "Ledger",  # the repository's own name
    "Blast radius",
    "Change risk",
    "Your ledger stops guessing",  # a marketing heading
    "Hotspots",
    "Response",  # an ordinary English word
    "Dead code",
    "Co-change",
    "Architecture",
    "Ingestion pipeline",  # named by a document, built nowhere
    "Audit trail",  # from the changelog
    "Query guide",
    "Bug magnet",
]

#: Five of the thirteen above are things a reader should never be shown, and
#: every one of them stays. This list is the planner's input, and the planner
#: discards whatever fails to bind to a real directory, so none of them costs
#: it anything. Rejecting them is the ranked view's job — the tests below
#: assert each is gone from *that*.


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


def by_term(repo: Path, **kwargs) -> dict[str, object]:
    return {t.term: t for t in extract_house_terms(repo, **kwargs)}


def test_the_ranked_view_keeps_only_what_the_code_also_spells(repo: Path) -> None:
    """The gate, end to end.

    Every term here is named by a document. What separates the survivors is
    whether the source prose uses the term too.
    """
    terms = [t.term for t in extract_house_terms(repo)]
    assert terms == [
        # Two documents name it; nothing else here is named twice.
        "Blast radius",
        # Then multi-word terms, then single-word ones. Within each group,
        # how much of the code uses the term.
        "Bug magnet",
        "Change risk",
        "Co-change",
        "Dead code",
        "Hotspots",
        "Reconciliation",
    ]


def test_a_term_the_documents_name_but_the_code_never_uses_is_dropped(
    repo: Path,
) -> None:
    """A marketed feature with nothing behind it does not become vocabulary."""
    assert "Ingestion pipeline" not in by_term(repo)


def test_a_claim_is_not_a_name(repo: Path) -> None:
    """A heading with a pronoun is a sentence about the reader."""
    assert "Your ledger stops guessing" not in by_term(repo)


def test_the_repository_does_not_define_its_own_name(repo: Path) -> None:
    """It leads every frequency count in every repository and teaches nothing."""
    assert "Ledger" not in by_term(repo)


def test_release_notes_are_not_mined(repo: Path) -> None:
    """The changelog names "Audit trail" under a plain heading, and the code
    spells it, so only skipping the document keeps it out."""
    assert "Audit trail" not in by_term(repo)


def test_an_ordinary_word_does_not_become_a_term(repo: Path) -> None:
    """ "Response" is named by a heading and used by the code, and is still not
    a subsystem: no acronym shape, no internal capital, no directory."""
    assert "Response" not in by_term(repo)


def test_a_single_word_the_codebase_spells_in_a_directory_survives(
    repo: Path,
) -> None:
    """The same test :func:`bind_terms` applies. "Hotspots" has src/hotspots/."""
    assert "Hotspots" in by_term(repo)


def test_a_numbered_heading_with_a_gloss_yields_its_name(repo: Path) -> None:
    """ "2 · Reconciliation: matching two ledgers" names Reconciliation.

    Without stripping the number the whole heading is rejected for carrying a
    digit, and the section contributes nothing.
    """
    assert "Reconciliation" in by_term(repo)


def test_ranking_is_by_how_many_documents_name_the_term(repo: Path) -> None:
    terms = extract_house_terms(repo)
    assert terms[0].term == "Blast radius"
    assert terms[0].doc_frequency == 2
    assert all(t.doc_frequency == 1 for t in terms[1:])


def test_code_frequency_is_counted_but_never_added_to_document_frequency(
    repo: Path,
) -> None:
    """Kept apart on purpose.

    Summing them lets a word appearing in hundreds of docstrings outrank the
    name of a real subsystem. "Response" would win on a sum and is not a term
    at all.
    """
    blast = by_term(repo)["Blast radius"]
    assert blast.doc_frequency == 2
    assert blast.code_frequency == 1


def test_typescript_prose_counts_as_the_code_spelling_the_term(repo: Path) -> None:
    """A JSDoc block is a maintainer writing about a unit of code.

    Reading only Python scores a TypeScript repository zero on every term and
    the gate then rejects its entire vocabulary.
    """
    assert by_term(repo)["Bug magnet"].code_frequency == 1
    assert by_term(repo)["Dead code"].code_frequency == 1


def test_vendored_code_is_not_the_repository_talking_about_itself(
    repo: Path,
) -> None:
    """node_modules names "Ingestion pipeline"; it must not gate it in."""
    assert "Ingestion pipeline" not in by_term(repo)


def test_a_heading_yields_its_following_sentence_as_the_definition(
    repo: Path,
) -> None:
    blast = by_term(repo)["Blast radius"]
    assert blast.definition == (
        "Blast radius is the set of files a change can reach through the import graph."
    )
    assert blast.definition_source == "README.md"


def test_a_bolded_lead_in_yields_its_definition(repo: Path) -> None:
    dead = by_term(repo)["Dead code"]
    assert dead.definition == "code no import path reaches."
    assert dead.definition_source == "README.md"


def test_a_documented_definition_wins_over_a_code_one(repo: Path) -> None:
    """The docs are where a term is explained for a reader; a docstring is
    where it is explained for whoever is editing that file."""
    magnet = by_term(repo)["Bug magnet"]
    assert magnet.definition == (
        "A file that has absorbed an unusual share of the repository's bug fixes."
    )
    assert magnet.definition_source == "docs/guide.md"


def test_code_prose_supplies_a_definition_the_documents_did_not(repo: Path) -> None:
    """ "Co-change" is a bolded term with no dash, so the documents name it and
    never explain it. The code does."""
    co = by_term(repo)["Co-change"]
    assert co.definition == ("Co-change counts how often two files land in the same commit.")
    assert co.definition_source == "src/history.py"


def test_a_term_records_every_document_that_names_it(repo: Path) -> None:
    blast = by_term(repo)["Blast radius"]
    assert blast.source_paths[:2] == ("README.md", "ARCHITECTURE.md")
    assert "src/analysis.py" in blast.source_paths


def test_source_paths_are_repository_relative_and_posix(repo: Path) -> None:
    assert by_term(repo)["Bug magnet"].source_paths[0] == "docs/guide.md"


def test_a_term_with_no_sentence_anywhere_reports_none(repo: Path) -> None:
    """No definition beats a confident wrong one."""
    hotspots = by_term(repo)["Hotspots"]
    assert hotspots.code_frequency == 1, "the code does mention it"
    assert hotspots.definition is None, "but never in a sentence that defines it"
    assert hotspots.definition_source is None


def test_a_term_the_codebase_defines_is_marked_as_a_symbol(repo: Path) -> None:
    """Only a real symbol may be rendered in backticks.

    Grounding strips the backticks off a symbol-shaped token it cannot
    resolve, so a coined term dressed as code is demoted mid-page — silently,
    which is the failure shape this repository has shipped before.
    """
    found = by_term(repo, known_symbols={"Blast radius"})
    assert found["Blast radius"].is_indexed_symbol is True
    assert found["Bug magnet"].is_indexed_symbol is False


def test_without_a_symbol_set_nothing_claims_to_be_one(repo: Path) -> None:
    assert all(not t.is_indexed_symbol for t in extract_house_terms(repo))


def test_extraction_is_deterministic(repo: Path) -> None:
    """It runs on the keyless path and has to give the same answer twice."""
    assert extract_house_terms(repo) == extract_house_terms(repo)


def test_house_terms_on_a_repository_with_no_docs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Finding nothing is reported, not returned as if it were a finding."""
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert extract_house_terms(tmp_path) == []
    assert "no house terms found" in caplog.text


def test_documents_read_but_nothing_survived_is_a_different_report(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A repository whose code this cannot read looks identical to one with no
    docs unless the two are logged apart."""
    (tmp_path / "README.md").write_text(
        "# Widget\n\n## Blast radius\n\nSomething about it here.\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert extract_house_terms(tmp_path) == []
    assert "none survived" in caplog.text


# ---------------------------------------------------------------------------
# reStructuredText
# ---------------------------------------------------------------------------
#
# Read as markdown, a reStructuredText document yields no headings at all —
# not an error, just silence. Of the repositories checked by hand, flask,
# requests and django were all in this position.

_RST_README = """\
=======
Ledger
=======

A ledger for small shops.

Blast radius
============

Blast radius is the set of files a change can reach through the import graph.

Change risk
-----------

.. note::

   Change risk scores a diff against the history of the files it touches.

Empty section
-------------

Bug magnet
----------

See :doc:`hotspots` and the `guide <https://example.com>`_ for ``details``.

**Dead code** -- code no import path reaches.

Not a heading
some prose that happens to sit above a short rule
---
"""

_RST_GUIDE = """\
Co-change
~~~~~~~~~

Co-change counts how often two files land in the same commit.
"""


@pytest.fixture
def rst_repo(tmp_path: Path) -> Path:
    root = tmp_path / "ledger"
    root.mkdir()
    (root / "README.rst").write_text(_RST_README, encoding="utf-8")
    docs = root / "docs"
    docs.mkdir()
    (docs / "guide.rst").write_text(_RST_GUIDE, encoding="utf-8")

    src = root / "src"
    src.mkdir()
    (src / "analysis.py").write_text(_SRC_ANALYSIS, encoding="utf-8")
    (src / "history.py").write_text(_SRC_HISTORY, encoding="utf-8")
    (src / "magnet.py").write_text('"""Bug magnet scoring."""\n', encoding="utf-8")
    (src / "dead.py").write_text('"""Dead code sweep."""\n', encoding="utf-8")
    return root


def test_restructuredtext_headings_are_read(rst_repo: Path) -> None:
    found = {t.term for t in extract_house_terms(rst_repo)}
    assert {"Blast radius", "Change risk", "Bug magnet", "Co-change"} <= found


def test_a_docs_directory_of_rst_is_mined(rst_repo: Path) -> None:
    """docs/guide.rst is the only place "Co-change" is named."""
    co = {t.term: t for t in extract_house_terms(rst_repo)}["Co-change"]
    assert co.source_paths[0] == "docs/guide.rst"


def test_an_rst_section_yields_its_following_sentence(rst_repo: Path) -> None:
    blast = {t.term: t for t in extract_house_terms(rst_repo)}["Blast radius"]
    assert blast.definition == (
        "Blast radius is the set of files a change can reach through the import graph."
    )
    assert blast.definition_source == "README.rst"


def test_a_directive_is_markup_not_prose(rst_repo: Path) -> None:
    """A directive and everything indented under it is markup.

    ``.. note::`` happens to hold a sentence, so taking it would look right
    here. ``.. code-block::`` holds code and ``.. toctree::`` holds
    filenames, and the scan cannot tell them apart from the outside — so it
    takes none of them.
    """
    by_name = {t.term: t for t in extract_house_terms(rst_repo)}
    assert "Note" not in by_name
    # The only sentence about "Change risk" in the document sits inside the
    # note, and is not taken. The definition it ends up with comes from the
    # code, which is the documented fallback.
    risk = by_name["Change risk"]
    assert risk.definition_source == "src/analysis.py"


def test_the_rst_definition_scan_stops_at_the_next_section(rst_repo: Path) -> None:
    """ "Empty section" has no prose of its own; the sentence below it belongs
    to "Bug magnet"."""
    magnet = {t.term: t for t in extract_house_terms(rst_repo)}["Bug magnet"]
    assert magnet.definition is not None
    assert "Bug magnet" not in (magnet.definition or "")


def test_rst_roles_and_links_are_reduced_to_their_text(rst_repo: Path) -> None:
    """A definition is prose for a reader, not markup."""
    magnet = {t.term: t for t in extract_house_terms(rst_repo)}["Bug magnet"]
    assert ":doc:" not in (magnet.definition or "")
    assert "``" not in (magnet.definition or "")
    assert "<https://example.com>" not in (magnet.definition or "")


def test_a_short_rule_does_not_make_the_line_above_a_title(rst_repo: Path) -> None:
    """reStructuredText requires the underline to be at least as long as its
    title. Without that check every table rule and thematic break in the
    document becomes a heading."""
    assert "Not a heading" not in {t.term for t in extract_house_terms(rst_repo)}


def test_an_overlined_title_is_read_once(rst_repo: Path) -> None:
    """The overline form is punctuation above *and* below. It must not yield
    a term made of punctuation, and must not double-count the title."""
    terms = [t.term for t in extract_house_terms(rst_repo)]
    assert all(t.strip("=-~ ") for t in terms)


# ---------------------------------------------------------------------------
# A reStructuredText section that opens with markup has no definition
# ---------------------------------------------------------------------------
#
# Mined from django, whose ``docs/index.txt`` is a table of contents: its
# "Models" section opens with a grid of ``:doc:`` links, and the sentence taken
# from it was ``:doc:`Introduction to models <topics/db/models>` |``. Markup, a
# wrong definition, and a stray pipe that breaks any table it lands in.

_RST_TOC = """\
Models
======

:doc:`Introduction to models <topics/db/models>` |
:doc:`Field types <ref/models/fields>` |
:doc:`Indexes <ref/models/indexes>`

* **Hotspots:**
  :doc:`Churn ranking <ref/hotspots>` |
  :doc:`Bug history <ref/history>`

Co-change
~~~~~~~~~

Co-change counts how often two files land in the same commit.

Bug magnet
~~~~~~~~~~

A bug magnet is a file that has absorbed an unusual share of the
repository's bug fixes over its lifetime.

Blast radius
~~~~~~~~~~~~

+------------------+-------------------+
| Column           | Meaning           |
+==================+===================+
| depth            | hops from the seed|
+------------------+-------------------+
"""


@pytest.fixture
def toc_repo(tmp_path: Path) -> Path:
    root = tmp_path / "ledger"
    root.mkdir()
    docs = root / "docs"
    docs.mkdir()
    (docs / "index.rst").write_text(_RST_TOC, encoding="utf-8")

    src = root / "src"
    src.mkdir()
    (src / "history.py").write_text(_SRC_HISTORY, encoding="utf-8")
    hotspots = src / "hotspots"
    hotspots.mkdir()
    (hotspots / "rank.py").write_text('"""Churn ranking for hotspots."""\n', encoding="utf-8")
    (src / "magnet.py").write_text('"""Scoring for the bug magnet view."""\n', encoding="utf-8")
    # Both spell their term, so both pass the code gate; neither *opens* a
    # sentence with it, so neither supplies a definition of its own. What is
    # left is whatever the document offered — which is the point of the tests.
    (src / "radius.py").write_text(
        '"""Rendering for the blast radius overlay."""\n', encoding="utf-8"
    )
    # A directory named for the term: how a one-word term earns its place.
    models = src / "models"
    models.mkdir()
    (models / "base.py").write_text('"""Declarations that back the models."""\n', encoding="utf-8")
    return root


def test_a_toctree_entry_is_not_a_definition(toc_repo: Path) -> None:
    """A section whose opening lines are list markup defines nothing.

    Reporting no sentence is correct. Reporting a link out of a contents grid
    as the repository's own definition is confidently wrong, which is the one
    outcome this module exists to avoid.
    """
    models = {t.term: t for t in extract_house_terms(toc_repo)}["Models"]
    assert models.definition is None


def test_a_definition_never_carries_a_table_cell_separator(toc_repo: Path) -> None:
    """Definitions are rendered into markdown tables downstream."""
    for term in extract_house_terms(toc_repo):
        assert "|" not in (term.definition or "")


def test_a_grid_table_row_is_not_a_definition(toc_repo: Path) -> None:
    blast = {t.term: t for t in extract_house_terms(toc_repo)}["Blast radius"]
    assert blast.definition is None


def test_a_bolded_label_alone_on_its_line_claims_no_definition(toc_repo: Path) -> None:
    """A lead-in and the sentence defining it share a line.

    A bolded label standing alone above a list of links defines nothing, and
    reading the next line as its meaning is how ``:doc:`` markup became a
    definition of "Hotspots".
    """
    hotspots = {t.term: t for t in extract_house_terms(toc_repo)}["Hotspots"]
    assert hotspots.definition is None


def test_a_hard_wrapped_sentence_is_read_whole(toc_repo: Path) -> None:
    """reStructuredText wraps prose at the column, so a line is not a sentence.

    Reading one line is how django's Forms section defined itself as "Django
    provides a rich framework to facilitate the creation of forms and the" —
    the author's sentence, cut where the editor wrapped it.
    """
    magnet = {t.term: t for t in extract_house_terms(toc_repo)}["Bug magnet"]
    assert magnet.definition == (
        "A bug magnet is a file that has absorbed an unusual share of the "
        "repository's bug fixes over its lifetime."
    )


def test_prose_after_the_section_title_is_still_taken(toc_repo: Path) -> None:
    """Skipping markup must not skip the sentence that follows it."""
    co = {t.term: t for t in extract_house_terms(toc_repo)}["Co-change"]
    assert co.definition == "Co-change counts how often two files land in the same commit."


def test_extract_terms_ignores_rst_headings(rst_repo: Path) -> None:
    """The planner's input does not move. README.rst is already read today and
    its bolded lead-ins already count; its section titles do not, and this
    change must not alter that.
    """
    assert extract_terms(rst_repo) == ["Dead code"]


# ---------------------------------------------------------------------------
# reStructuredText written under .txt
# ---------------------------------------------------------------------------
#
# Sphinx's ``source_suffix`` is a project setting, and a project is as free to
# set it to ``.txt`` as to leave it at ``.rst``. django writes its whole
# ``docs/`` tree that way. A glob that reads only ``.rst`` skips every one of
# those documents and reports the repository as having nothing to say.


@pytest.fixture
def txt_docs_repo(tmp_path: Path) -> Path:
    """A repository whose only document is reStructuredText named ``.txt``."""
    root = tmp_path / "ledger"
    root.mkdir()
    docs = root / "docs"
    docs.mkdir()
    (docs / "guide.txt").write_text(_RST_GUIDE, encoding="utf-8")

    src = root / "src"
    src.mkdir()
    (src / "history.py").write_text(_SRC_HISTORY, encoding="utf-8")
    return root


def test_a_docs_directory_of_txt_is_read_as_restructuredtext(
    txt_docs_repo: Path,
) -> None:
    """The extension is the only thing that differs from ``guide.rst``."""
    co = {t.term: t for t in extract_house_terms(txt_docs_repo)}["Co-change"]
    assert co.source_paths[0] == "docs/guide.txt"
    assert co.definition == "Co-change counts how often two files land in the same commit."


def test_a_txt_document_that_is_not_restructuredtext_yields_no_sections(
    tmp_path: Path,
) -> None:
    """Guessing wrong is bounded.

    A ``.txt`` with no title underlines has no sections, so the reST scan
    returns nothing rather than reading its prose lines as names.
    """
    root = tmp_path / "ledger"
    root.mkdir()
    docs = root / "docs"
    docs.mkdir()
    (docs / "requirements.txt").write_text(
        "Sphinx==7.2.6\nfuro==2024.1.29\nCo-change==1.0\n", encoding="utf-8"
    )
    src = root / "src"
    src.mkdir()
    (src / "history.py").write_text(_SRC_HISTORY, encoding="utf-8")

    assert extract_house_terms(root) == []


def test_release_notes_named_txt_are_skipped(tmp_path: Path) -> None:
    """A changelog is the largest and least useful document in a repository
    whatever extension it carries."""
    root = tmp_path / "ledger"
    root.mkdir()
    docs = root / "docs"
    docs.mkdir()
    (docs / "changelog.txt").write_text(
        "Co-change\n~~~~~~~~~\n\nCo-change counts commits.\n", encoding="utf-8"
    )
    src = root / "src"
    src.mkdir()
    (src / "history.py").write_text(_SRC_HISTORY, encoding="utf-8")

    assert extract_house_terms(root) == []


def test_extract_terms_does_not_read_txt_documents(txt_docs_repo: Path) -> None:
    """The planner's input does not move. It mines markdown only, and the
    binding it performs is measured against exactly that corpus."""
    assert extract_terms(txt_docs_repo) == []


def test_a_hard_wrapped_markdown_sentence_is_read_whole(tmp_path: Path) -> None:
    """A README wraps at the column as readily as a ``.txt`` does.

    The reStructuredText scan has rejoined wrapped lines since it learned to;
    the markdown scan read one line and returned the author's sentence cut
    where their editor wrapped it. That fragment then trails off mid-thought,
    which every consumer renders as prose the repository wrote.
    """
    root = tmp_path / "ledger"
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text(
        "# Ledger\n"
        "\n"
        "## Blast radius\n"
        "\n"
        "Blast radius is the set of accounts a posting can reach through the\n"
        "ledger graph.\n"
        "\n"
        "## Dead code\n"
        "\n"
        "Dead code is a rule no posting path reaches.\n",
        encoding="utf-8",
    )
    (root / "src" / "engine.py").write_text(
        '"""Blast radius and dead code for the ledger."""\n', encoding="utf-8"
    )

    by_term = {t.term: t for t in extract_house_terms(root)}
    assert by_term["Blast radius"].definition == (
        "Blast radius is the set of accounts a posting can reach through the ledger graph."
    )
    # A sentence that already fits on one line is unaffected.
    assert by_term["Dead code"].definition == "Dead code is a rule no posting path reaches."


# ---------------------------------------------------------------------------
# Which files may be cited as where a capability is written
# ---------------------------------------------------------------------------
#
# The capability table's third column is headed "Where it is written", and the
# glossary cites the same path. Both were rendering scratch harnesses under a
# git-excluded ``local-stash/`` and a competitive-positioning document in
# ``docs/`` as the authority for what a term means. Nothing downstream can tell
# a planning document from a subsystem guide by its headings, so the source set
# is decided here.


@pytest.fixture
def repo_with_scratch(repo: Path) -> Path:
    """The fixture repository plus the two shapes that leaked."""
    (repo / ".gitignore").write_text("local-stash/\n", encoding="utf-8")
    stash = repo / "local-stash" / "harnesses"
    stash.mkdir(parents=True)
    (stash / "_toggle.py").write_text(
        '"""Blast radius is whatever this throwaway harness says it is."""\n',
        encoding="utf-8",
    )
    (repo / "docs" / "BENCHMARKS.md").write_text(
        "# Benchmarks\n\n## Blast radius\n\nWe beat every competitor on blast radius.\n",
        encoding="utf-8",
    )
    return repo


def test_a_tree_the_repository_excludes_is_not_the_repository_talking(
    repo_with_scratch: Path,
) -> None:
    """A path the user told git to ignore is scratch work, not documentation.

    ``local-stash/`` is absent from the index, the file tree and every other
    surface, so citing it sends the reader to a file they cannot open.
    """
    prose, _ = _scan_tree(repo_with_scratch)
    assert not [rel for rel, _ in prose if rel.startswith("local-stash/")]


def test_an_excluded_harness_does_not_define_a_term(repo_with_scratch: Path) -> None:
    """The visible failure: the harness supplied the sentence and the citation."""
    term = by_term(repo_with_scratch)["Blast radius"]
    assert "local-stash" not in (term.definition_source or "")
    assert "local-stash" not in " ".join(term.source_paths)


def test_a_market_facing_document_is_not_mined(repo_with_scratch: Path) -> None:
    """Its headings are product claims, and they look exactly like subsystems."""
    assert "docs/BENCHMARKS.md" not in [
        p.relative_to(repo_with_scratch).as_posix() for p in _doc_paths(repo_with_scratch)
    ]


def test_excluding_scratch_does_not_cost_a_real_term(repo_with_scratch: Path) -> None:
    """The filter is aimed at the source set, not at the vocabulary.

    "Blast radius" is named by two real documents and spelled by real source,
    so it survives losing both leaked citations.
    """
    assert "Blast radius" in by_term(repo_with_scratch)
