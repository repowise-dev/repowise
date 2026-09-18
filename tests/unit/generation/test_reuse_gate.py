"""Cross-run reuse gate for model-written pages (issue #1089).

The headline defect: a run interrupted by a provider outage could never be
topped up cheaply. Two halves:

* the reuse gate keyed only on the rendered prompt's ``source_hash``, which
  drifts run to run because RAG context is rebuilt concurrently — so every
  full run re-billed every page ("pages are always regenerated");
* a stub left by the failed provider call was indistinguishable from a
  written page, so a later run could copy the placeholder over itself
  instead of actually writing it ("the missing ones are unreachable" —
  that half was closed by the stub fallback itself).

These tests pin the repaired gate: subject-keyed reuse that survives prompt
drift, and a hard refusal to reuse any row a model never wrote.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.generation.context_assembler import ContextAssembler, FilePageContext
from repowise.core.generation.models import STUB_FALLBACK_ERROR
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.page_generator.core import PriorPage
from repowise.core.providers.llm.mock import MockProvider


def _ctx(path: str = "pkg/resolvers/context.py") -> FilePageContext:
    return FilePageContext(
        file_path=path,
        language="python",
        docstring=None,
        symbols=[],
        imports=[],
        exports=[],
        pagerank_score=0.1,
        betweenness_score=0.0,
        community_id=0,
        dependents=[],
        dependencies=[],
        is_api_contract=False,
        is_entry_point=False,
        is_test=False,
        parse_errors=[],
        estimated_tokens=10,
    )


def _module_page(
    gen: PageGenerator,
    *,
    structural_key: str,
    contexts: list[FilePageContext],
    target_path: str = "pkg/resolvers",
    title: str = "Resolvers",
):
    return gen.generate_module_page(
        title,
        "python",
        contexts,
        nx.DiGraph(),
        target_path=target_path,
        structural_key=structural_key,
    )


async def test_subject_keyed_reuse_survives_prompt_drift(sample_config):
    """The "pages are always regenerated" half of #1089.

    Run 1 writes the page. Run 2 renders a *different* prompt for the same
    group — the assembled context moved (RAG rebuild, a dependency summary
    landing) — so the old source-hash-only gate missed and re-billed it. The
    subject key (the group's ``structural_key`` folded with the renderer
    fingerprint) is unchanged, so the page is reused without a provider call.
    """
    gen = PageGenerator(MockProvider(), ContextAssembler(sample_config), sample_config)
    first = await _module_page(gen, structural_key="concept-resolvers", contexts=[_ctx("a.py")])
    assert first.content_hash  # the key must actually be computed and stamped

    prior = {
        first.page_id: PriorPage(
            source_hash=first.source_hash,
            content_hash=first.content_hash,
            model_name=first.model_name,
            content=first.content,
            provider_name=first.provider_name,
        )
    }
    # Run 2: a different member file changes the rendered prompt (so the
    # source_hash would miss) while the group's structural identity holds.
    drift_provider = MockProvider()
    drift_gen = PageGenerator(
        drift_provider, ContextAssembler(sample_config), sample_config, prior_pages=prior
    )
    second = await _module_page(
        drift_gen,
        structural_key="concept-resolvers",
        contexts=[_ctx("a.py"), _ctx("b.py")],
    )

    assert second.source_hash != first.source_hash  # prompt drifted
    assert drift_provider.call_count == 0  # but the page was not re-billed
    assert second.metadata.get("reused_from_prior_run") is True
    assert second.content == first.content


async def test_a_stub_row_is_never_reused_on_a_top_up_run(sample_config):
    """The headline #1089 scenario: the missing pages are actually completed.

    Run 1's provider call failed, leaving a stub row
    (``provider_name='template'`` + ``STUB_FALLBACK_ERROR``). A later run
    asked for that page again must call the model — copying the placeholder
    over itself would keep the page unwritten forever while looking done.
    """
    gen = PageGenerator(MockProvider(), ContextAssembler(sample_config), sample_config)
    # Compute the exact subject key the generator will look up, so the match
    # would succeed if not for the stub guard.
    stub_key = gen._reuse_content_hash(
        "concept-resolvers",
        gen._model_page_fingerprint("module_page", "module_page.j2"),
    )
    prior = {
        "module_page:pkg/resolvers": PriorPage(
            source_hash="prompt-hash-from-run-1",
            content_hash=stub_key,
            model_name=gen._provider.model_name,
            content="# Resolvers\n\n(stub — the provider was down)",
            provider_name="template",  # never written by a model
        )
    }
    refill_provider = MockProvider()
    refill_gen = PageGenerator(
        refill_provider, ContextAssembler(sample_config), sample_config, prior_pages=prior
    )

    page = await _module_page(
        refill_gen, structural_key="concept-resolvers", contexts=[_ctx("a.py")]
    )

    assert refill_provider.call_count == 1
    assert page.metadata.get("reused_from_prior_run") is None
    assert page.content != "# Resolvers\n\n(stub — the provider was down)"
    assert STUB_FALLBACK_ERROR not in page.metadata


async def test_a_keyless_stub_row_is_never_reused_either(sample_config):
    """Same refusal for the other template row: an index-only wiki's stub.

    A user who ran ``init --no-prose`` then ``update --full`` must get real
    prose, not their own placeholder echoed back at them.
    """
    gen = PageGenerator(MockProvider(), ContextAssembler(sample_config), sample_config)
    prior = {
        "module_page:pkg/resolvers": PriorPage(
            source_hash="k",
            content_hash="",
            model_name=gen._provider.model_name,
            content="# Resolvers (template stub)",
            provider_name="template",
        )
    }
    provider = MockProvider()
    refill_gen = PageGenerator(
        provider, ContextAssembler(sample_config), sample_config, prior_pages=prior
    )

    page = await _module_page(
        refill_gen, structural_key="concept-resolvers", contexts=[_ctx("a.py")]
    )

    assert provider.call_count == 1
    assert page.metadata.get("reused_from_prior_run") is None


async def test_prompt_hash_gate_still_reuses_a_written_page(sample_config):
    """The historical gate keeps working for callers with no subject key.

    A pre-key row (``content_hash=''``) whose rendered prompt still hashes to
    the stored value under the same model is reused exactly as before — this
    is the path every existing store takes on its first run with the new
    code, and it must not regress.
    """
    gen = PageGenerator(MockProvider(), ContextAssembler(sample_config), sample_config)
    first = await _module_page(gen, structural_key="concept-resolvers", contexts=[_ctx("a.py")])
    prior = {
        first.page_id: PriorPage(
            source_hash=first.source_hash,
            content_hash="",  # pre-key row
            model_name=first.model_name,
            content=first.content,
            provider_name=first.provider_name,
        )
    }
    provider = MockProvider()
    reuse_gen = PageGenerator(
        provider, ContextAssembler(sample_config), sample_config, prior_pages=prior
    )
    second = await _module_page(
        reuse_gen, structural_key="concept-resolvers", contexts=[_ctx("a.py")]
    )

    assert provider.call_count == 0
    assert second.metadata.get("reused_from_prior_run") is True


async def test_a_different_model_never_reuses(sample_config):
    """The model name stays part of the gate: a provider switch (exactly what
    an outage forces) invalidates nothing about the *subject* key, but a
    different model writing the same page is a different page."""
    gen = PageGenerator(MockProvider(), ContextAssembler(sample_config), sample_config)
    first = await _module_page(gen, structural_key="concept-resolvers", contexts=[_ctx("a.py")])
    prior = {
        first.page_id: PriorPage(
            source_hash=first.source_hash,
            content_hash=first.content_hash,
            model_name=first.model_name,
            content=first.content,
            provider_name=first.provider_name,
        )
    }
    other = MockProvider(model="other-model")
    other_gen = PageGenerator(
        other, ContextAssembler(sample_config), sample_config, prior_pages=prior
    )
    page = await _module_page(
        other_gen, structural_key="concept-resolvers", contexts=[_ctx("a.py")]
    )

    assert other.call_count == 1
    assert page.metadata.get("reused_from_prior_run") is None
    assert page.content_hash == first.content_hash  # same subject, same key


async def test_reuse_key_changes_when_the_subject_changes(sample_config):
    """A group whose membership changed is a different page: the key must
    follow the subject, or a re-grouped module would silently keep prose
    written for the old one."""
    gen = PageGenerator(MockProvider(), ContextAssembler(sample_config), sample_config)
    first = await _module_page(gen, structural_key="concept-resolvers", contexts=[_ctx("a.py")])
    prior = {
        first.page_id: PriorPage(
            source_hash=first.source_hash,
            content_hash=first.content_hash,
            model_name=first.model_name,
            content=first.content,
            provider_name=first.provider_name,
        )
    }
    provider = MockProvider()
    regrouped_gen = PageGenerator(
        provider, ContextAssembler(sample_config), sample_config, prior_pages=prior
    )
    # The grouper re-cut the module: same target path, new membership key.
    page = await _module_page(
        regrouped_gen, structural_key="concept-resolvers-v2", contexts=[_ctx("a.py")]
    )

    assert page.content_hash != first.content_hash
    assert provider.call_count == 1
