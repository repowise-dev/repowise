"""Cross-run page reuse keyed on file content + generation settings.

The persistent cross-run cache historically keyed on ``source_hash`` —
SHA256 of the entire rendered prompt. The prompt embeds RAG context that is
rebuilt fresh (and populated concurrently) every run, so on a reindex even
unchanged files rendered a different prompt and every page hit the LLM.

The reuse key is ``sha256(file-bytes hash : generation fingerprint)`` where
the fingerprint folds every fixed input that shapes page content (template,
system prompt, language, style, harvest flag). Two invariants pinned here:

* an unchanged file with unchanged settings reuses even when the prompt
  drifts (``test_reuse_fires_when_prompt_drifts_but_file_unchanged``);
* any settings change that would alter page content busts reuse, exactly
  as the prompt-hash path used to guarantee.
"""

from __future__ import annotations

import dataclasses

import jinja2
import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator import PageGenerator, PriorPage
from repowise.core.providers.llm.mock import MockProvider

DRIFTED_PROMPT_HASH = "0" * 64  # never equals a real SHA256 of the prompt


def _make_gen(config, prior=None, language="en", jinja_env=None):
    provider = MockProvider()
    gen = PageGenerator(
        provider,
        ContextAssembler(config),
        config,
        prior_pages=prior,
        language=language,
        jinja_env=jinja_env,
    )
    return provider, gen


async def _generate(gen, parsed, graph, metrics, source_bytes):
    return await gen.generate_file_page(
        parsed,
        graph,
        metrics["pagerank"],
        metrics["betweenness"],
        metrics["community"],
        source_bytes,
    )


def _prior_from(page, *, model_name=None, content_hash=None, content=None):
    """PriorPage as the next run would reload it, with a deliberately drifted
    prompt hash so only the content-hash gate can fire."""
    return {
        page.page_id: PriorPage(
            source_hash=DRIFTED_PROMPT_HASH,
            model_name=model_name if model_name is not None else page.model_name,
            content=content if content is not None else page.content,
            content_hash=content_hash if content_hash is not None else page.content_hash,
        )
    }


async def _first_run_page(parsed, graph, metrics, source_bytes, config=None, **gen_kw):
    _, gen = _make_gen(config or GenerationConfig(), **gen_kw)
    return await _generate(gen, parsed, graph, metrics, source_bytes)


async def test_reuse_fires_when_prompt_drifts_but_file_unchanged(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """Unchanged file + same settings → reuse, even when the prompt differs."""
    parsed = dataclasses.replace(sample_parsed_file, content_hash="a" * 64)
    page = await _first_run_page(parsed, sample_graph, graph_metrics, sample_source_bytes)
    assert page.content_hash  # stamped for the next run

    provider2, gen2 = _make_gen(GenerationConfig(), prior=_prior_from(page))
    page2 = await _generate(gen2, parsed, sample_graph, graph_metrics, sample_source_bytes)

    assert provider2.call_count == 0  # reused — no LLM call
    assert gen2._reuse_count == 1
    assert page2.content == page.content
    assert page2.content_hash == page.content_hash  # key stays alive across persists


async def test_changed_file_regenerates(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """Different file bytes → no reuse; the page is regenerated fresh."""
    parsed_v1 = dataclasses.replace(sample_parsed_file, content_hash="a" * 64)
    page = await _first_run_page(parsed_v1, sample_graph, graph_metrics, sample_source_bytes)

    parsed_v2 = dataclasses.replace(sample_parsed_file, content_hash="b" * 64)
    prior = _prior_from(page, content="STALE PRIOR CONTENT")
    provider2, gen2 = _make_gen(GenerationConfig(), prior=prior)
    page2 = await _generate(gen2, parsed_v2, sample_graph, graph_metrics, sample_source_bytes)

    assert provider2.call_count == 1
    assert gen2._reuse_count == 0
    assert page2.content != "STALE PRIOR CONTENT"  # not stale-reused


async def test_model_change_busts_reuse(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    parsed = dataclasses.replace(sample_parsed_file, content_hash="a" * 64)
    page = await _first_run_page(parsed, sample_graph, graph_metrics, sample_source_bytes)

    prior = _prior_from(page, model_name="some-other-model")
    provider2, gen2 = _make_gen(GenerationConfig(), prior=prior)
    await _generate(gen2, parsed, sample_graph, graph_metrics, sample_source_bytes)

    assert provider2.call_count == 1
    assert gen2._reuse_count == 0


async def test_language_change_busts_reuse(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """The language instruction lives in the SYSTEM prompt, so neither the
    prompt hash nor the raw file hash sees it — the generation fingerprint
    must. en → fr on an unchanged file regenerates in French."""
    parsed = dataclasses.replace(sample_parsed_file, content_hash="a" * 64)
    page = await _first_run_page(parsed, sample_graph, graph_metrics, sample_source_bytes)

    provider2, gen2 = _make_gen(GenerationConfig(), prior=_prior_from(page), language="fr")
    await _generate(gen2, parsed, sample_graph, graph_metrics, sample_source_bytes)

    assert provider2.call_count == 1
    assert gen2._reuse_count == 0


async def test_harvest_toggle_busts_reuse(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """Turning decision harvesting on changes what pages carry (the harvest
    directive is system-prompt-only) — unchanged files must regenerate."""
    parsed = dataclasses.replace(sample_parsed_file, content_hash="a" * 64)
    cfg_off = GenerationConfig(harvest_decisions=False)
    page = await _first_run_page(
        parsed, sample_graph, graph_metrics, sample_source_bytes, config=cfg_off
    )

    cfg_on = GenerationConfig(harvest_decisions=True)
    provider2, gen2 = _make_gen(cfg_on, prior=_prior_from(page))
    await _generate(gen2, parsed, sample_graph, graph_metrics, sample_source_bytes)

    assert provider2.call_count == 1
    assert gen2._reuse_count == 0


async def test_template_change_busts_reuse(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """A repowise upgrade that changes file_page.j2 must regenerate unchanged
    files — otherwise old-format pages persist until the file itself changes."""
    parsed = dataclasses.replace(sample_parsed_file, content_hash="a" * 64)
    env_v1 = jinja2.Environment(
        loader=jinja2.DictLoader({"file_page.j2": "V1 {{ ctx.file_path }}"}),
        undefined=jinja2.Undefined,
        autoescape=False,
    )
    page = await _first_run_page(
        parsed, sample_graph, graph_metrics, sample_source_bytes, jinja_env=env_v1
    )

    env_v2 = jinja2.Environment(
        loader=jinja2.DictLoader({"file_page.j2": "V2 improved {{ ctx.file_path }}"}),
        undefined=jinja2.Undefined,
        autoescape=False,
    )
    provider2, gen2 = _make_gen(GenerationConfig(), prior=_prior_from(page), jinja_env=env_v2)
    await _generate(gen2, parsed, sample_graph, graph_metrics, sample_source_bytes)

    assert provider2.call_count == 1
    assert gen2._reuse_count == 0

    # Control: identical template env → reuse fires.
    env_v1_again = jinja2.Environment(
        loader=jinja2.DictLoader({"file_page.j2": "V1 {{ ctx.file_path }}"}),
        undefined=jinja2.Undefined,
        autoescape=False,
    )
    provider3, gen3 = _make_gen(GenerationConfig(), prior=_prior_from(page), jinja_env=env_v1_again)
    await _generate(gen3, parsed, sample_graph, graph_metrics, sample_source_bytes)
    assert provider3.call_count == 0
    assert gen3._reuse_count == 1


async def test_prior_without_content_hash_falls_back_to_prompt_hash(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """Back-compat: a prior page from an old pages.json (no content_hash)
    still reuses via the prompt-hash path when the prompt is identical."""
    parsed = dataclasses.replace(sample_parsed_file, content_hash="a" * 64)
    page = await _first_run_page(parsed, sample_graph, graph_metrics, sample_source_bytes)

    prior = {
        page.page_id: PriorPage(
            source_hash=page.source_hash,
            model_name=page.model_name,
            content=page.content,
        )
    }
    provider2, gen2 = _make_gen(GenerationConfig(), prior=prior)
    await _generate(gen2, parsed, sample_graph, graph_metrics, sample_source_bytes)

    assert provider2.call_count == 0
    assert gen2._reuse_count == 1


async def test_prior_without_content_hash_and_drifted_prompt_regenerates(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """Back-compat priors get no content-hash shortcut: drifted prompt → regen."""
    parsed = dataclasses.replace(sample_parsed_file, content_hash="a" * 64)
    page = await _first_run_page(parsed, sample_graph, graph_metrics, sample_source_bytes)

    prior = _prior_from(page, content_hash="")
    provider2, gen2 = _make_gen(GenerationConfig(), prior=prior)
    await _generate(gen2, parsed, sample_graph, graph_metrics, sample_source_bytes)

    assert provider2.call_count == 1
    assert gen2._reuse_count == 0


# ---------------------------------------------------------------------------
# Wiki styles
# ---------------------------------------------------------------------------


async def test_style_change_busts_content_hash_reuse(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    parsed = dataclasses.replace(sample_parsed_file, content_hash="a" * 64)
    page = await _first_run_page(
        parsed,
        sample_graph,
        graph_metrics,
        sample_source_bytes,
        config=GenerationConfig(wiki_style="comprehensive"),
    )

    provider2, gen2 = _make_gen(GenerationConfig(wiki_style="caveman"), prior=_prior_from(page))
    await _generate(gen2, parsed, sample_graph, graph_metrics, sample_source_bytes)

    assert provider2.call_count == 1
    assert gen2._reuse_count == 0


async def test_style_change_busts_reuse_both_directions(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """Asymmetric case: prior generated under an ACTIVE style, current run
    default — must regenerate too (the fingerprint differs both ways)."""
    parsed = dataclasses.replace(sample_parsed_file, content_hash="a" * 64)
    page = await _first_run_page(
        parsed,
        sample_graph,
        graph_metrics,
        sample_source_bytes,
        config=GenerationConfig(wiki_style="caveman"),
    )

    provider2, gen2 = _make_gen(GenerationConfig(), prior=_prior_from(page))
    await _generate(gen2, parsed, sample_graph, graph_metrics, sample_source_bytes)

    assert provider2.call_count == 1
    assert gen2._reuse_count == 0


async def test_active_style_reuse_key_is_stable_across_runs(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """Same active style + unchanged file → same reuse key → reuse fires."""
    parsed = dataclasses.replace(sample_parsed_file, content_hash="a" * 64)
    config = GenerationConfig(wiki_style="caveman")
    page = await _first_run_page(
        parsed, sample_graph, graph_metrics, sample_source_bytes, config=config
    )

    provider2, gen2 = _make_gen(config, prior=_prior_from(page))
    await _generate(gen2, parsed, sample_graph, graph_metrics, sample_source_bytes)

    assert provider2.call_count == 0
    assert gen2._reuse_count == 1


# ---------------------------------------------------------------------------
# Key shape / non-file pages
# ---------------------------------------------------------------------------


async def test_generated_page_carries_reuse_key_not_raw_file_hash(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """The persisted key must be the settings-folded composite, never the
    bare file hash — a bare hash would survive template/language changes."""
    parsed = dataclasses.replace(sample_parsed_file, content_hash="c" * 64)
    page = await _first_run_page(parsed, sample_graph, graph_metrics, sample_source_bytes)
    assert page.content_hash
    assert page.content_hash != "c" * 64
    assert len(page.content_hash) == 64  # sha256 hex


async def test_page_without_content_hash_unaffected(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """Pages whose ParsedFile has no content hash keep prompt-hash semantics."""
    no_hash = dataclasses.replace(sample_parsed_file, content_hash="")
    page = await _first_run_page(no_hash, sample_graph, graph_metrics, sample_source_bytes)
    assert page.content_hash == ""

    # A prior with a content_hash must NOT match a current empty key.
    prior = _prior_from(page, content_hash="a" * 64)
    provider2, gen2 = _make_gen(GenerationConfig(), prior=prior)
    await _generate(gen2, no_hash, sample_graph, graph_metrics, sample_source_bytes)
    assert provider2.call_count == 1
    assert gen2._reuse_count == 0


@pytest.mark.parametrize("style", [None, "caveman"])
def test_reuse_key_deterministic_across_generator_instances(sample_parsed_file, style):
    """Two independently constructed generators with identical settings must
    compute the same key — that's what makes cross-run reuse possible."""
    parsed = dataclasses.replace(sample_parsed_file, content_hash="d" * 64)
    cfg = GenerationConfig(wiki_style=style) if style else GenerationConfig()
    _, gen_a = _make_gen(cfg)
    _, gen_b = _make_gen(GenerationConfig(wiki_style=style) if style else GenerationConfig())
    assert gen_a._reuse_content_hash(parsed) == gen_b._reuse_content_hash(parsed)
