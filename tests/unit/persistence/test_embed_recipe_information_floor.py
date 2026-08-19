"""A page too thin to be worth a search slot gets no vector either.

The full-text index already refuses these. The vector arm has to refuse the
same ones, and on the same input, or the exclusion does nothing: retrieval
fetches a fixed number of rows from each arm *before* it filters anything, so
a page held out of one and kept in the other is still fetched and still
displaces a page that could have answered.

The page is never touched. It stays in ``wiki_pages``, still resolves as a
link target, and a reader who arrives at it still learns the file exists.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.information_floor import (
    INFORMATION_FLOOR_ENV,
    meets_information_floor,
    pages_denied_a_vector,
    reset_pages_denied_a_vector,
)
from repowise.core.persistence.vector_store import embed_item

# A real spotlight for a bare interface, verbatim in shape: a heading and a
# metadata strip, which the floor strips to nothing.
SKELETON = (
    "# packages.api-client.src.types.graph.GraphLike\n\n"
    "**Kind:** interface | **Defined in:** `packages/api-client/src/types/graph.ts` "
    "| **Estimated complexity:** 1\n"
)

SUBSTANTIAL = (
    "## Overview\n\n"
    "The walker resolves each import against the package manifest before it "
    "descends, so a symlinked workspace member is visited once rather than "
    "once per alias that reaches it. Cycles are broken on the real path, not "
    "the alias, which is why two aliases of one directory cannot both claim "
    "to own a module.\n"
)


@pytest.fixture(autouse=True)
def _floor(monkeypatch: pytest.MonkeyPatch):
    """Each test states its own floor; the default is off."""
    monkeypatch.delenv(INFORMATION_FLOOR_ENV, raising=False)
    reset_pages_denied_a_vector()
    yield
    reset_pages_denied_a_vector()


def _item(content: str):
    return embed_item(
        "symbol_spotlight:a.ts::GraphLike",
        title="GraphLike",
        page_type="symbol_spotlight",
        target_path="packages/api-client/src/types/graph.ts",
        summary="",
        content=content,
    )


def test_a_skeleton_page_gets_no_vector(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "300")

    assert _item(SKELETON) is None


def test_a_substantial_page_is_still_embedded(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "300")

    item = _item(SUBSTANTIAL)

    assert item is not None
    assert item[0] == "symbol_spotlight:a.ts::GraphLike"


def test_the_floor_is_off_by_default():
    """Installing this must change nobody's index until a value is set."""
    assert _item(SKELETON) is not None


def test_both_arms_judge_the_same_page_the_same_way(monkeypatch: pytest.MonkeyPatch):
    """The two arms measure ``content``, and must measure the same thing.

    A page kept by one arm and dropped by the other is still fetched, so the
    exclusion buys nothing and the disagreement is pure confusion.
    """
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "300")

    for content in (SKELETON, SUBSTANTIAL):
        assert (_item(content) is not None) == meets_information_floor(content)


def test_a_held_back_page_is_counted(monkeypatch: pytest.MonkeyPatch):
    """A smaller index has innocent explanations; the deliberate part is named."""
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "300")

    _item(SKELETON)
    _item(SKELETON)
    _item(SUBSTANTIAL)

    assert pages_denied_a_vector() == 2


def test_nothing_is_counted_when_the_floor_is_off():
    _item(SKELETON)

    assert pages_denied_a_vector() == 0


def test_a_blank_title_still_raises_below_the_floor(monkeypatch: pytest.MonkeyPatch):
    """The two rules are about different failures and must not mask each other.

    A missing title is a writer that lost it — a defect to fix — while being
    below the floor is a page working as intended. Returning ``None`` for a
    titleless thin page would file the first as the second and lose it.
    """
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "300")

    with pytest.raises(ValueError, match="no title"):
        embed_item(
            "file_page:a.py",
            title="   ",
            page_type="file_page",
            target_path="a.py",
            summary="",
            content=SKELETON,
        )
