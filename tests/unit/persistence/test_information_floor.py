"""Pages that say almost nothing stay out of the search index.

Search fetches a fixed number of rows before it filters anything, so every
indexed page holds a slot whether or not it can answer a question. A file
page whose every section is a placeholder holds one of those slots against a
page that could have used it.

The page is kept. Only its index row goes.
"""

from __future__ import annotations

import logging

import pytest

from repowise.core.persistence.information_floor import (
    DEFAULT_INFORMATION_FLOOR,
    INFORMATION_FLOOR_ENV,
    # 300 is the first value worth sweeping; the tests that exercise the floor
    # set it explicitly, because shipping it on would change every index.
    information_floor,
    meets_information_floor,
    substantive_text,
)

# A real file page from the measured corpus, at 186 substantive characters:
# every section below the Overview is a placeholder.
SKELETON = """# packages/vscode/esbuild.mjs

## Overview

`packages/vscode/esbuild.mjs` is a javascript source file in the Application layer.

## Public API

_No public symbols were extracted from this file._

## Depends on

_No internal dependencies resolved._

## Used by

_No internal callers were resolved for this file._

## Usage Notes

**Layer:** Application | **Role:** entry_point

---

*Built from the code itself: parsed symbols, the import graph, git history and
the knowledge graph. Every statement here is checked against the source rather
than written about it.*
"""

SWEEP_FLOOR = 300


@pytest.fixture(autouse=True)
def floor_on(monkeypatch):
    """The floor ships off. These tests are about what it does when on."""
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, str(SWEEP_FLOOR))


SUBSTANTIAL = (
    "# packages/core/search.py\n\n## Overview\n\n"
    + "The full-text index is an FTS5 virtual table rebuilt on demand. " * 12
)


# --------------------------------------------------------------------------
# What counts as substance
# --------------------------------------------------------------------------


def test_a_heading_is_not_content():
    assert substantive_text("## Overview\n### Details") == ""


def test_a_placeholder_line_is_not_content():
    assert substantive_text("_No internal dependencies resolved._") == ""


def test_a_metadata_field_line_is_not_content():
    assert substantive_text("**Kind:** interface | **Defined in:** `a/b.ts`") == ""


def test_a_table_is_not_content():
    assert substantive_text("| Symbol | Kind |\n| --- | --- |\n| `config` | variable |") == ""


def test_fenced_code_is_not_content():
    """The signature block is the file restating itself, not prose about it."""
    assert substantive_text("```\ninterface GraphLike\n```") == ""


def test_the_provenance_trailer_is_not_content():
    """It is on 97% of pages in the measured corpus and says the same thing.

    Left in, it adds ~180 characters to every page and no floor discriminates.
    """
    trailer = "*Built from the code itself: parsed symbols, the import graph.*"
    assert substantive_text(trailer) == ""


def test_bullet_lists_are_content():
    """They look like structure and are not.

    On a file page the dependency and caller lists are the most specific thing
    it says, and on a layer or onboarding page the bullets are the page.
    Dropping them takes the exclusion from 3% of the measured corpus to 36%
    and starts excluding the orientation pages a reader most needs to find.
    """
    text = substantive_text("- `packages/types/src/graph.ts`\n- `packages/ui/src/panel.tsx`")
    assert "packages/types/src/graph.ts" in text
    assert "packages/ui/src/panel.tsx" in text


def test_prose_survives_stripping_line_by_line():
    assert substantive_text(SKELETON) == (
        "`packages/vscode/esbuild.mjs` is a javascript source file in the Application layer."
    )


# --------------------------------------------------------------------------
# The floor itself
# --------------------------------------------------------------------------


def test_a_skeleton_page_is_below_the_floor():
    assert meets_information_floor(SKELETON) is False


def test_a_real_page_is_above_the_floor():
    assert meets_information_floor(SUBSTANTIAL) is True


def test_the_floor_reads_the_environment(monkeypatch):
    """Tunable without a redeploy: the right value is a property of a corpus."""
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "50")
    assert information_floor() == 50
    assert meets_information_floor(SKELETON) is True


def test_a_floor_of_zero_admits_everything(monkeypatch):
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "0")
    assert meets_information_floor("") is True


def test_an_unparseable_floor_falls_back_to_the_default(monkeypatch):
    """A typo in a deployment variable must not silently change the index."""
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "three hundred")
    assert information_floor() == DEFAULT_INFORMATION_FLOOR


def test_a_negative_floor_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "-1")
    assert information_floor() == DEFAULT_INFORMATION_FLOOR


# --------------------------------------------------------------------------
# What it does to the index
# --------------------------------------------------------------------------


@pytest.fixture
async def fts(async_engine):
    from repowise.core.persistence import FullTextSearch

    index = FullTextSearch(async_engine)
    await index.ensure_index()
    return index


async def test_a_thin_page_is_not_searchable(fts):
    await fts.index(
        "file_page:esbuild.mjs",
        "File: esbuild.mjs",
        SKELETON,
        summary="",
        target_path="packages/vscode/esbuild.mjs",
    )

    assert await fts.search("javascript source file") == []


async def test_a_substantial_page_still_is(fts):
    await fts.index(
        "file_page:search.py",
        "File: search.py",
        SUBSTANTIAL,
        summary="",
        target_path="packages/core/search.py",
    )

    assert [r.page_id for r in await fts.search("FTS5 virtual table")] == ["file_page:search.py"]


async def test_a_page_that_falls_below_the_floor_loses_the_row_it_had(fts):
    """Otherwise the exclusion only ever applies to pages that never existed.

    A page regenerated thinner than it was keeps serving its old text, which
    is worse than the skeleton it became: the index and the page disagree.
    """
    await fts.index("file_page:a.py", "File: a.py", SUBSTANTIAL, summary="", target_path="a.py")
    assert await fts.search("FTS5 virtual table") != []

    await fts.index("file_page:a.py", "File: a.py", SKELETON, summary="", target_path="a.py")

    assert await fts.search("FTS5 virtual table") == []
    assert await fts.search("javascript source file") == []


async def test_the_run_counts_what_it_held_out(fts, caplog):
    """A counter, not a line per page — a corpus write would print thousands."""
    with caplog.at_level(logging.INFO, logger="repowise.core.persistence.search"):
        for i in range(4):
            await fts.index(
                f"file_page:{i}.py", f"File: {i}.py", SKELETON, summary="", target_path=f"{i}.py"
            )
        fts.log_floor_exclusions()

    assert fts.skipped_below_floor == 4

    totals = [
        r.getMessage()
        for r in caplog.records
        if "fts_pages_below_information_floor" in r.getMessage()
    ]
    assert len(totals) == 1
    assert "count=4" in totals[0]
    assert f"floor={SWEEP_FLOOR}" in totals[0]


async def test_nothing_excluded_reports_nothing(fts, caplog):
    """A repository whose pages are all substantial says nothing, not zero."""
    with caplog.at_level(logging.INFO, logger="repowise.core.persistence.search"):
        await fts.index("file_page:a.py", "File: a.py", SUBSTANTIAL, summary="", target_path="a.py")
        fts.log_floor_exclusions()

    assert fts.skipped_below_floor == 0
    assert [r for r in caplog.records if "below_information_floor" in r.getMessage()] == []


def test_a_star_bullet_is_content_not_emphasis():
    """``* item`` and ``*emphasis*`` open with the same character.

    The one with a space after it is a list; the one without is the trailer.
    """
    assert "keep me" in substantive_text("* keep me")
    assert substantive_text("*drop me*") == ""


def test_a_multi_line_italic_block_is_consumed_whole():
    """The trailer wraps, and only its first line carries the open marker."""
    assert substantive_text("*first line\nsecond line\nthird line*") == ""
    assert substantive_text("*trailer\nwraps here*\n\nreal prose") == "real prose"


def test_a_bold_field_line_does_not_open_an_italic_block():
    """``**Kind:**`` starts with a star and never closes one.

    Read as an unterminated italic it opened a block that consumed every
    remaining line, so a page carrying one metadata strip measured as saying
    nothing at all — which excluded every spotlight and every API contract in
    the corpus this was measured against.
    """
    page = "**Kind:** class | **Defined in:** `a/b.py`\n\n## Overview\n\nReal prose here."

    assert substantive_text(page) == "Real prose here."


def test_the_floor_is_off_unless_it_is_set(monkeypatch):
    """Installing this must not change anyone's index.

    Excluding pages is a deletion, and what it costs is a retrieval
    measurement rather than an argument. The mechanism ships ahead of the
    number so the number can be swept.
    """
    monkeypatch.delenv(INFORMATION_FLOOR_ENV, raising=False)

    assert DEFAULT_INFORMATION_FLOOR == 0
    assert information_floor() == 0
    assert meets_information_floor(SKELETON) is True
    assert meets_information_floor("") is True


async def test_a_batch_holds_out_only_the_thin_pages(fts):
    await fts.index_many(
        [
            ("file_page:esbuild.mjs", "File: esbuild.mjs", SKELETON, "", "esbuild.mjs"),
            ("file_page:search.py", "File: search.py", SUBSTANTIAL, "", "search.py"),
        ]
    )

    assert await fts.list_indexed_ids() == {"file_page:search.py"}
    assert fts.skipped_below_floor == 1


async def test_a_batch_drops_the_row_of_a_page_that_thinned_out(fts):
    """The delete runs for every id in the batch, insert only for those above."""
    await fts.index("file_page:a.py", "File: a.py", SUBSTANTIAL, summary="", target_path="a.py")

    await fts.index_many([("file_page:a.py", "File: a.py", SKELETON, "", "a.py")])

    assert await fts.list_indexed_ids() == set()
    assert await fts.search("FTS5 virtual table") == []


async def test_a_batch_whose_last_entry_is_thin_leaves_no_row(fts):
    """Last-entry-wins has to hold when the last entry is the excluded one."""
    await fts.index_many(
        [
            ("file_page:a.py", "File: a.py", SUBSTANTIAL, "", "a.py"),
            ("file_page:a.py", "File: a.py", SKELETON, "", "a.py"),
        ]
    )

    assert await fts.list_indexed_ids() == set()
