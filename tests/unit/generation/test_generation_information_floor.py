"""Generation skips the vector for a page below the information floor.

The recipe returning ``None`` is not the fix on its own — the caller acting
on it is. Generation is the path that writes almost every vector a user ever
searches, so a caller that appended ``None`` into the embed batch would fail
at the store instead of holding the page back, and one that ignored the
return would embed everything exactly as before.
"""

from __future__ import annotations

import pytest

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.page_generator.orchestrate import _embed_item
from repowise.core.generation.report import GenerationReport
from repowise.core.persistence.information_floor import INFORMATION_FLOOR_ENV

SKELETON = (
    "# packages.api-client.src.types.graph.GraphLike\n\n"
    "**Kind:** interface | **Defined in:** `packages/api-client/src/types/graph.ts`\n"
)

SUBSTANTIAL = (
    "## Overview\n\n"
    "Resolves each import against the package manifest before descending, so "
    "a symlinked workspace member is visited once rather than once per alias "
    "that reaches it. Cycles break on the real path, never the alias, which "
    "is why two aliases of one directory cannot both claim to own a module. "
    "The manifest is read once per package and cached for the walk, because "
    "re-reading it per import made a large monorepo spend most of the walk in "
    "the filesystem rather than in the resolver.\n"
)


@pytest.fixture(autouse=True)
def _floor_off_by_default(monkeypatch: pytest.MonkeyPatch):
    """Each test states its own floor rather than inheriting a stray one."""
    monkeypatch.delenv(INFORMATION_FLOOR_ENV, raising=False)


@pytest.fixture
def tally():
    """The process-wide count of pages held back, cleared around the test.

    Imported inside the fixture, not at module scope, so the two behaviour
    tests below stay collectable against a build that has no tally — which is
    what makes their failure read as the change rather than an absent name.
    """
    from repowise.core.persistence.information_floor import (
        pages_denied_a_vector,
        reset_pages_denied_a_vector,
    )

    reset_pages_denied_a_vector()
    yield pages_denied_a_vector
    reset_pages_denied_a_vector()


def _page(page_id: str, content: str) -> GeneratedPage:
    return GeneratedPage(
        page_id=page_id,
        page_type="symbol_spotlight",
        title=page_id,
        content=content,
        source_hash="",
        model_name="mock",
        provider_name="mock",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=1,
        target_path="packages/api-client/src/types/graph.ts",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        summary="",
    )


def test_generation_holds_back_a_thin_page(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "300")

    assert _embed_item(_page("symbol_spotlight:thin", SKELETON)) is None


def test_generation_still_embeds_a_real_page(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "300")

    assert _embed_item(_page("symbol_spotlight:real", SUBSTANTIAL)) is not None


def test_the_run_reports_how_many_it_held_back(monkeypatch: pytest.MonkeyPatch, tally):
    """Otherwise raising the floor is indistinguishable from an embedder
    quietly dropping work: both leave the index smaller than the wiki."""
    monkeypatch.setenv(INFORMATION_FLOOR_ENV, "300")
    pages = [
        _page("symbol_spotlight:thin-1", SKELETON),
        _page("symbol_spotlight:thin-2", SKELETON),
        _page("symbol_spotlight:real", SUBSTANTIAL),
    ]
    for page in pages:
        _embed_item(page)

    report = GenerationReport.from_pages(pages)

    assert report.pages_denied_a_vector == 2
    # The page itself is untouched: it is in the wiki, it is only out of the
    # index. A report that dropped it from the page counts would say the run
    # produced fewer pages than it did.
    assert sum(report.pages_by_type.values()) == 3


def test_the_report_says_zero_when_the_floor_is_off(tally):
    pages = [_page("symbol_spotlight:thin", SKELETON)]
    for page in pages:
        _embed_item(page)

    assert GenerationReport.from_pages(pages).pages_denied_a_vector == 0
