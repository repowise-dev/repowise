"""A model page whose provider call fails falls back to its structural stub.

Dropping the page was what made a provider outage unrecoverable (issue #1089).
Scope resolution runs over persisted page records, so a page no run ever wrote
is invisible to ``generate`` and to ``update`` alike and cannot be asked for
again. Falling back to the stub leaves a row that reads as unwritten, which is
the state ``generate`` already knows how to repair.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import (
    MODEL_WRITTEN_PAGE_TYPES,
    STUB_FALLBACK_ERROR,
    GeneratedPage,
)
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.scope import load_page_records
from repowise.core.providers.llm.mock import MockProvider


@dataclass
class _Row:
    """Duck-typed persisted Page row, as ``load_page_records`` reads one."""

    id: str
    page_type: str
    target_path: str
    provider_name: str
    freshness_status: str = "fresh"
    metadata_json: str = "{}"


class _OutageProvider(MockProvider):
    """A provider that fails the way a real outage does: every call raises."""

    async def generate(self, *args, **kwargs):
        raise RuntimeError("upstream 529 overloaded")


@pytest.fixture
def outage_gen(sample_config) -> PageGenerator:
    return PageGenerator(_OutageProvider(), ContextAssembler(sample_config), sample_config)


async def _overview(gen: PageGenerator, sample_repo_structure) -> GeneratedPage:
    return await gen.generate_repo_overview(
        sample_repo_structure,
        pagerank={},
        sccs=[],
        community={},
        repo_name="demo",
    )


async def test_provider_failure_returns_a_page_instead_of_raising(
    outage_gen, sample_repo_structure
):
    page = await _overview(outage_gen, sample_repo_structure)

    assert isinstance(page, GeneratedPage)
    assert page.page_id == "repo_overview:demo"
    assert page.content.strip()


async def test_fallback_page_reads_as_unwritten(outage_gen, sample_repo_structure):
    """``provider_name='template'`` is the one signal every stub check reads.

    ``generate`` selects its work with it, ``_page_stats`` counts it, and
    ``docs_mode`` flips on it. A fallback stamped with the real provider would
    claim the wiki is fully written and stop offering to fill the gap.
    """
    page = await _overview(outage_gen, sample_repo_structure)

    assert page.provider_name == "template"
    assert page.input_tokens == 0
    assert page.output_tokens == 0


async def test_fallback_page_is_selected_by_generate_as_a_stub(outage_gen, sample_repo_structure):
    """The end the whole change exists for: the page can be asked for again."""
    page = await _overview(outage_gen, sample_repo_structure)

    (record,) = load_page_records(
        [
            _Row(
                id=page.page_id,
                page_type=page.page_type,
                target_path=page.target_path,
                provider_name=page.provider_name,
            )
        ]
    )

    assert record.is_template
    assert record.page_type in MODEL_WRITTEN_PAGE_TYPES


async def test_fallback_carries_the_provider_error(outage_gen, sample_repo_structure):
    """The level runner reads this back to record the page as failed."""
    page = await _overview(outage_gen, sample_repo_structure)

    assert "upstream 529 overloaded" in page.metadata[STUB_FALLBACK_ERROR]


async def test_deterministic_stub_is_not_marked_as_a_failure(sample_config, sample_repo_structure):
    """A deterministic run meant to write a stub. Nothing failed, so the job
    must not be told one did, and the page still belongs in the resume ledger.
    """
    from dataclasses import replace

    config = replace(sample_config, deterministic=True)
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)

    page = await _overview(gen, sample_repo_structure)

    assert page.provider_name == "template"
    assert STUB_FALLBACK_ERROR not in page.metadata
