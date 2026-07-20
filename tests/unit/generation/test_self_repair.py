"""Bounded self-repair for hallucinated symbol references.

When ``_validate_symbol_references`` flags >= ``repair_warning_threshold``
invalid backtick refs on a tier-1 file page, the generator re-calls the
provider exactly once with a corrective note naming the bad refs and keeps
whichever draft validates cleaner. Reused prior-run pages are never retried.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig, compute_page_id
from repowise.core.generation.page_generator import PageGenerator, PriorPage
from repowise.core.ingestion.models import ParsedFile
from repowise.core.providers.llm.base import GeneratedResponse
from repowise.core.providers.llm.mock import MockProvider

from .conftest import _make_file_info, _make_symbol

# `PhantomThing` / `GhostHelper` match no symbol, import, or export of the
# parsed file below, are >2 chars, mixed-case, and path-free, so the
# validator flags them. `Thing` is the file's real (and only) symbol.
BAD_CONTENT = "## Overview\n\nUses `PhantomThing` and `GhostHelper` for math."
CLEAN_CONTENT = "## Overview\n\nUses `Thing` for math."


def _parsed(path: str = "pkg/mod.py") -> ParsedFile:
    return ParsedFile(
        file_info=_make_file_info(path=path),
        symbols=[_make_symbol(name="Thing", file_path=path)],
        imports=[],
        exports=["Thing"],
        docstring="mod",
        parse_errors=[],
        content_hash="h",
    )


def _gen_and_ctx(responses: list[GeneratedResponse], **config_overrides):
    config = GenerationConfig(**config_overrides)
    provider = MockProvider(responses=responses)
    gen = PageGenerator(provider, ContextAssembler(config), config)
    parsed = _parsed()
    g = nx.DiGraph()
    g.add_node(parsed.file_info.path)
    ctx = gen._assembler.assemble_file_page(parsed, g, {}, {}, {}, b"x = 1\n")
    return gen, provider, parsed, ctx


async def test_retry_adopts_cleaner_draft_and_sums_tokens() -> None:
    gen, provider, parsed, ctx = _gen_and_ctx(
        [
            GeneratedResponse(BAD_CONTENT, 100, 50),
            GeneratedResponse(CLEAN_CONTENT, 200, 80),
        ]
    )

    page = await gen._generate_file_page_from_ctx(parsed, ctx)

    assert provider.call_count == 2
    assert page.content == CLEAN_CONTENT
    assert page.metadata["self_repair"] == "improved"
    assert "hallucination_warnings" not in page.metadata
    # Both calls' tokens land on the page so cost accounting is honest.
    assert page.input_tokens == 300
    assert page.output_tokens == 130


async def test_retry_prompt_names_bad_refs_on_top_of_original() -> None:
    gen, provider, parsed, ctx = _gen_and_ctx(
        [
            GeneratedResponse(BAD_CONTENT, 100, 50),
            GeneratedResponse(CLEAN_CONTENT, 200, 80),
        ]
    )

    await gen._generate_file_page_from_ctx(parsed, ctx)

    original = provider.calls[0]["user_prompt"]
    retry = provider.calls[1]["user_prompt"]
    assert retry.startswith(original)
    assert "`PhantomThing`" in retry
    assert "`GhostHelper`" in retry


async def test_retry_keeps_original_when_not_improved() -> None:
    gen, provider, parsed, ctx = _gen_and_ctx(
        [
            GeneratedResponse(BAD_CONTENT, 100, 50),
            GeneratedResponse(BAD_CONTENT, 200, 80),  # retry just as bad
        ]
    )

    page = await gen._generate_file_page_from_ctx(parsed, ctx)

    assert provider.call_count == 2
    assert page.content == BAD_CONTENT
    assert page.metadata["self_repair"] == "kept_original"
    assert sorted(page.metadata["hallucination_warnings"]) == ["GhostHelper", "PhantomThing"]
    assert page.input_tokens == 300  # retry cost still counted


async def test_no_retry_below_threshold() -> None:
    one_warning = "Uses `PhantomThing` and `Thing`."
    gen, provider, parsed, ctx = _gen_and_ctx([GeneratedResponse(one_warning, 100, 50)])

    page = await gen._generate_file_page_from_ctx(parsed, ctx)

    assert provider.call_count == 1
    assert "self_repair" not in page.metadata
    assert page.metadata["hallucination_warnings"] == ["PhantomThing"]


async def test_zero_threshold_disables_retry() -> None:
    gen, provider, parsed, ctx = _gen_and_ctx(
        [GeneratedResponse(BAD_CONTENT, 100, 50)],
        repair_warning_threshold=0,
    )

    page = await gen._generate_file_page_from_ctx(parsed, ctx)

    assert provider.call_count == 1
    assert "self_repair" not in page.metadata
    assert len(page.metadata["hallucination_warnings"]) == 2


async def test_reused_prior_page_is_never_retried() -> None:
    gen, provider, parsed, ctx = _gen_and_ctx([GeneratedResponse(CLEAN_CONTENT, 100, 50)])
    gen._prior_pages = {
        compute_page_id("file_page", parsed.file_info.path): PriorPage(
            source_hash="unused",
            model_name=provider.model_name,
            content=BAD_CONTENT,
            content_hash=gen._reuse_content_hash(parsed),
        )
    }

    page = await gen._generate_file_page_from_ctx(parsed, ctx)

    assert provider.call_count == 0  # reuse gate hit, no LLM call at all
    assert page.content == BAD_CONTENT
    assert "self_repair" not in page.metadata
    assert len(page.metadata["hallucination_warnings"]) == 2
