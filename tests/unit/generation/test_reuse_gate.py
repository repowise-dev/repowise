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

from dataclasses import replace

import networkx as nx

from repowise.core.generation.architecture_map import build_system_map
from repowise.core.generation.context_assembler import ContextAssembler, FilePageContext
from repowise.core.generation.models import STUB_FALLBACK_ERROR
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.page_generator.core import PriorPage
from repowise.core.generation.page_generator.reuse import group_material, model_reuse_key
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
    **kwargs,
):
    return gen.generate_module_page(
        title,
        "python",
        contexts,
        nx.DiGraph(),
        target_path=target_path,
        structural_key=structural_key,
        **kwargs,
    )


def _prior_from(page) -> dict[str, PriorPage]:
    return {
        page.page_id: PriorPage(
            source_hash=page.source_hash,
            content_hash=page.content_hash,
            model_name=page.model_name,
            content=page.content,
            provider_name=page.provider_name,
            metadata=page.metadata,
        )
    }


async def test_subject_keyed_reuse_survives_prompt_drift(sample_config):
    """The "pages are always regenerated" half of #1089.

    Run 1 writes the page. Run 2 renders a *different* prompt for the same
    group — the assembled context moved (RAG rebuild, a dependency summary
    landing) — so the old source-hash-only gate missed and re-billed it. The
    subject key (the group's ``structural_key`` and its members' bytes, folded
    with the renderer fingerprint) is unchanged, so the page is reused without
    a provider call.
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
    # Run 2: context around the group moved (another member's page summary
    # landed), so the rendered prompt and its source_hash change, while the
    # group's membership and file bytes hold.
    drift_provider = MockProvider()
    drift_gen = PageGenerator(
        drift_provider, ContextAssembler(sample_config), sample_config, prior_pages=prior
    )
    second = await _module_page(
        drift_gen,
        structural_key="concept-resolvers",
        contexts=[_ctx("a.py")],
        page_summaries={"a.py": "Resolves context for a request."},
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
    stub_key = model_reuse_key(
        gen, group_material("concept-resolvers", None, None), "module_page", "module_page.j2"
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

    assert page.content_hash == stub_key  # the subject matched; only the guard refused
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


async def test_a_changed_member_file_regenerates_the_module_page(sample_config):
    """Membership alone is not the subject: an edited member file's bytes
    change the key, so its module page is rewritten rather than reused."""
    gen = PageGenerator(MockProvider(), ContextAssembler(sample_config), sample_config)
    first = await _module_page(
        gen,
        structural_key="concept-resolvers",
        contexts=[_ctx("a.py")],
        members=["a.py"],
        source_map={"a.py": b"def resolve(): return 1"},
    )
    provider = MockProvider()
    edited_gen = PageGenerator(
        provider, ContextAssembler(sample_config), sample_config, prior_pages=_prior_from(first)
    )
    page = await _module_page(
        edited_gen,
        structural_key="concept-resolvers",
        contexts=[_ctx("a.py")],
        members=["a.py"],
        source_map={"a.py": b"def resolve(): return 2"},
    )

    assert page.content_hash != first.content_hash
    assert provider.call_count == 1
    assert page.metadata.get("reused_from_prior_run") is None


def _system_map(store: str = "core/store.py"):
    return build_system_map(
        {"cli/main.py": "python", "core/engine.py": "python", store: "python"},
        [("cli/main.py", "core/engine.py", "imports"), ("core/engine.py", store, "calls")],
        repo_name="demo",
        manifests=["cli/pyproject.toml", "core/pyproject.toml"],
        entry_points=["cli/main.py"],
    )


async def _overview(gen, repo_structure, *, system_map, readme: bytes, git_meta_map=None):
    return await gen.generate_repo_overview(
        repo_structure,
        pagerank={},
        sccs=[],
        community={},
        git_meta_map=git_meta_map,
        repo_name="demo",
        system_map=system_map,
        source_map={"README.md": readme},
    )


def _evidence_config(sample_config):
    return replace(sample_config, source_evidence_files={"repo_overview": ("README.md",)})


async def test_overview_reuse_follows_its_map_and_evidence(sample_config, sample_repo_structure):
    """The overview reuses across prompt drift alone (git activity moved), and
    regenerates when the system map or a quoted evidence file changes."""
    config = _evidence_config(sample_config)
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    first = await _overview(
        gen, sample_repo_structure, system_map=_system_map(), readme=b"Demo indexes code."
    )
    prior = _prior_from(first)

    def _fresh():
        provider = MockProvider()
        return provider, PageGenerator(
            provider, ContextAssembler(config), config, prior_pages=prior
        )

    provider, drift_gen = _fresh()
    await _overview(
        drift_gen,
        sample_repo_structure,
        system_map=_system_map(),
        readme=b"Demo indexes code.",
        git_meta_map={"cli/main.py": {"file_path": "cli/main.py", "commit_count_90d": 3}},
    )
    assert provider.call_count == 0

    provider, readme_gen = _fresh()
    await _overview(
        readme_gen, sample_repo_structure, system_map=_system_map(), readme=b"Demo ships docs."
    )
    assert provider.call_count == 1

    provider, map_gen = _fresh()
    await _overview(
        map_gen,
        sample_repo_structure,
        system_map=_system_map("core/cache.py"),
        readme=b"Demo indexes code.",
    )
    assert provider.call_count == 1
