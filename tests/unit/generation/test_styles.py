"""Tests for the wiki-style system (generation/styles + page_generator wiring).

The headline test is ``test_style_change_invalidates_cross_run_cache`` — it pins the
feature's load-bearing contract: switching styles must invalidate the persistent
cross-run cache so ``repowise update`` regenerates pages in the new style. If that
test ever goes green while a style change is silently reused, the feature is broken
no matter what else passes (see WIKI_STYLES_PLAN.md §2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator import PageGenerator, PriorPage
from repowise.core.generation.styles import (
    DEFAULT_STYLE,
    ONBOARDING_PAGE_TYPE,
    is_known_style,
    list_styles,
    resolve_style,
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BUILTIN_NAMES = ["comprehensive", "caveman", "reference", "tutorial"]


@pytest.mark.parametrize("name", BUILTIN_NAMES)
def test_builtin_styles_resolve(name):
    spec = resolve_style(name)
    assert spec.name == name
    assert spec.description


def test_default_style_is_comprehensive():
    assert DEFAULT_STYLE == "comprehensive"
    assert resolve_style(None).name == "comprehensive"


def test_unknown_style_falls_back_to_default():
    assert resolve_style("does-not-exist").name == DEFAULT_STYLE
    assert resolve_style("").name == DEFAULT_STYLE


def test_resolve_is_case_insensitive():
    assert resolve_style("CAVEMAN").name == "caveman"
    assert resolve_style("  Reference ").name == "reference"


def test_is_known_style():
    assert is_known_style("caveman")
    assert not is_known_style("nope")
    assert not is_known_style(None)


def test_list_styles_covers_builtins():
    names = {s.name for s in list_styles()}
    assert names == set(BUILTIN_NAMES)


# ---------------------------------------------------------------------------
# StyleSpec semantics
# ---------------------------------------------------------------------------


def test_comprehensive_is_inert():
    """The default style must contribute nothing — byte-identical to pre-feature."""
    spec = resolve_style("comprehensive")
    assert spec.is_active is False
    assert spec.fingerprint == ""
    assert spec.user_prompt_prefix(is_onboarding=False) == ""
    assert spec.user_prompt_prefix(is_onboarding=True) == ""
    assert spec.system_prompt_suffix(is_onboarding=False) == ""


def test_active_style_has_prefix_and_suffix():
    spec = resolve_style("caveman")
    assert spec.is_active is True
    assert spec.fingerprint
    prefix = spec.user_prompt_prefix(is_onboarding=False)
    assert prefix.startswith("<!-- repowise-style:caveman")
    assert spec.fingerprint in prefix
    assert spec.system_prompt_suffix(is_onboarding=False)


def test_prefix_carries_fingerprint_marker():
    """The marker must embed the fingerprint so any style edit busts the hash."""
    spec = resolve_style("reference")
    assert f"fp:{spec.fingerprint}" in spec.user_prompt_prefix(is_onboarding=False)


def test_onboarding_condense_policy():
    """Per-style D9 policy: caveman condenses onboarding, reference does not."""
    caveman = resolve_style("caveman")
    reference = resolve_style("reference")

    # caveman condenses onboarding → prefix + suffix apply
    assert caveman.user_prompt_prefix(is_onboarding=True) != ""
    assert caveman.system_prompt_suffix(is_onboarding=True) != ""

    # reference keeps onboarding narrative → no prefix/suffix on onboarding pages
    assert reference.user_prompt_prefix(is_onboarding=True) == ""
    assert reference.system_prompt_suffix(is_onboarding=True) == ""
    # ...but reference still applies to ordinary pages
    assert reference.user_prompt_prefix(is_onboarding=False) != ""


def test_distinct_styles_have_distinct_fingerprints():
    fps = {resolve_style(n).fingerprint for n in ("caveman", "reference", "tutorial")}
    assert len(fps) == 3


def test_fingerprint_changes_when_directive_changes():
    from repowise.core.generation.styles.spec import StyleSpec

    a = StyleSpec(name="x", description="d", user_directive="alpha")
    b = StyleSpec(name="x", description="d", user_directive="beta")
    assert a.fingerprint != b.fingerprint


def test_fingerprint_changes_when_system_note_changes():
    """A note-only change must still bust the hash (it rides in the user marker)."""
    from repowise.core.generation.styles.spec import StyleSpec

    a = StyleSpec(name="x", description="d", system_note="note-a")
    b = StyleSpec(name="x", description="d", system_note="note-b")
    assert a.fingerprint != b.fingerprint
    # The note change is observable in the user-prompt marker too.
    assert a.user_prompt_prefix(is_onboarding=False) != b.user_prompt_prefix(is_onboarding=False)


# ---------------------------------------------------------------------------
# PageGenerator wiring
# ---------------------------------------------------------------------------


async def test_config_threads_style_into_generator(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    from repowise.core.providers.llm.mock import MockProvider

    config = GenerationConfig(wiki_style="caveman")
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    assert gen._style.name == "caveman"


async def test_comprehensive_prompt_is_unchanged_vs_no_style(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """Default-style rendering must not alter the prompt the model sees."""
    from repowise.core.providers.llm.mock import MockProvider

    config = GenerationConfig(wiki_style="comprehensive")
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    ctx = gen._assembler.assemble_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )
    rendered = gen._render("file_page.j2", ctx=ctx)
    assert not rendered.startswith("<!-- repowise-style")


async def test_active_style_prepends_directive_to_prompt(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    from repowise.core.providers.llm.mock import MockProvider

    config = GenerationConfig(wiki_style="caveman")
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    ctx = gen._assembler.assemble_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )
    rendered = gen._render("file_page.j2", ctx=ctx)
    assert rendered.startswith("<!-- repowise-style:caveman")
    assert "CAVEMAN" in rendered


def _make_gen(config, source_bytes, prior=None):
    from repowise.core.providers.llm.mock import MockProvider

    provider = MockProvider()
    gen = PageGenerator(provider, ContextAssembler(config), config, prior_pages=prior)
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


async def test_same_style_reuses_cross_run_cache(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """Baseline: identical inputs + same style → cross-run cache hit (no LLM call)."""
    config = GenerationConfig(wiki_style="caveman")
    _, gen1 = _make_gen(config, sample_source_bytes)
    page = await _generate(
        gen1, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
    )

    prior = {
        page.page_id: PriorPage(
            source_hash=page.source_hash,
            model_name=page.model_name,
            content=page.content,
        )
    }
    provider2, gen2 = _make_gen(config, sample_source_bytes, prior=prior)
    await _generate(gen2, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes)
    assert provider2.call_count == 0  # reused, no provider call
    assert gen2._reuse_count == 1


async def test_style_change_invalidates_cross_run_cache(
    sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """THE CONTRACT: a style change must miss the cache and regenerate the page.

    Generate under ``comprehensive``, hand the result to a generator running
    ``caveman`` as a prior page, and assert the page is regenerated (provider
    called, nothing reused). This is exactly what `repowise update` does after a
    style switch.
    """
    base_cfg = GenerationConfig(wiki_style="comprehensive")
    _, gen_base = _make_gen(base_cfg, sample_source_bytes)
    page = await _generate(
        gen_base, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
    )

    prior = {
        page.page_id: PriorPage(
            source_hash=page.source_hash,
            model_name=page.model_name,
            content=page.content,
        )
    }
    new_cfg = GenerationConfig(wiki_style="caveman")
    provider2, gen2 = _make_gen(new_cfg, sample_source_bytes, prior=prior)
    page2 = await _generate(
        gen2, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
    )

    assert provider2.call_count == 1  # regenerated, not reused
    assert gen2._reuse_count == 0
    assert page2.source_hash != page.source_hash  # style folded into the hash


async def test_onboarding_page_type_constant_matches_system_prompts():
    """ONBOARDING_PAGE_TYPE must match the key used by the onboarding generators."""
    from repowise.core.generation.page_generator import SYSTEM_PROMPTS

    assert ONBOARDING_PAGE_TYPE in SYSTEM_PROMPTS


# ---------------------------------------------------------------------------
# Custom styles (Phase 5)
# ---------------------------------------------------------------------------


def _write_custom_style(repo: Path, name: str, body: str) -> None:
    d = repo / ".repowise" / "styles" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "style.yaml").write_text(body, encoding="utf-8")


def test_custom_style_resolves(tmp_path):
    _write_custom_style(
        tmp_path,
        "terse",
        "description: My terse style\nuser_directive: Be very terse.\n"
        "onboarding_condenses: true\n",
    )
    spec = resolve_style("terse", repo_path=tmp_path)
    assert spec.name == "terse"
    assert spec.is_builtin is False
    assert spec.is_active is True
    assert spec.onboarding_condenses is True
    assert "Be very terse." in spec.user_prompt_prefix(is_onboarding=False)


def test_custom_style_listed_and_known(tmp_path):
    _write_custom_style(tmp_path, "terse", "system_note: Be terse.\n")
    assert is_known_style("terse", tmp_path)
    names = {s.name for s in list_styles(tmp_path)}
    assert "terse" in names
    assert {"comprehensive", "caveman", "reference", "tutorial"} <= names


def test_custom_style_unknown_without_repo_path(tmp_path):
    """Custom styles require repo_path; without it they don't resolve."""
    _write_custom_style(tmp_path, "terse", "user_directive: Terse.\n")
    assert resolve_style("terse").name == DEFAULT_STYLE
    assert not is_known_style("terse")


def test_custom_style_empty_is_rejected(tmp_path):
    """A style with neither directive nor note is inert → not a usable style."""
    _write_custom_style(tmp_path, "empty", "description: nothing here\n")
    assert not is_known_style("empty", tmp_path)
    assert resolve_style("empty", repo_path=tmp_path).name == DEFAULT_STYLE


def test_custom_style_name_traversal_rejected(tmp_path):
    """Unsafe names never touch the filesystem."""
    assert resolve_style("../evil", repo_path=tmp_path).name == DEFAULT_STYLE
    assert not is_known_style("../evil", tmp_path)
    assert not is_known_style("a/b", tmp_path)


def test_custom_style_directive_is_length_bounded(tmp_path):
    from repowise.core.generation.styles.registry import _MAX_DIRECTIVE_CHARS

    huge = "x" * (_MAX_DIRECTIVE_CHARS + 5000)
    _write_custom_style(tmp_path, "big", f"user_directive: {huge}\n")
    spec = resolve_style("big", repo_path=tmp_path)
    assert len(spec.user_directive) == _MAX_DIRECTIVE_CHARS


def test_custom_style_template_dir_detected(tmp_path):
    _write_custom_style(tmp_path, "tpl", "user_directive: Terse.\n")
    (tmp_path / ".repowise" / "styles" / "tpl" / "templates").mkdir()
    spec = resolve_style("tpl", repo_path=tmp_path)
    assert spec.template_dir is not None
    assert spec.template_dir.name == "templates"


def test_builtin_takes_precedence_over_custom(tmp_path):
    """A custom dir named like a built-in must not shadow the built-in."""
    _write_custom_style(tmp_path, "caveman", "user_directive: hijack\n")
    spec = resolve_style("caveman", repo_path=tmp_path)
    assert spec.is_builtin is True
    assert "hijack" not in spec.user_directive


async def test_custom_style_reaches_rendered_prompt(
    tmp_path, sample_parsed_file, sample_graph, graph_metrics, sample_source_bytes
):
    """PageGenerator(repo_path=...) resolves a custom style and injects its voice."""
    from repowise.core.providers.llm.mock import MockProvider

    _write_custom_style(
        tmp_path, "terse", "user_directive: TERSE_MARKER be brief.\n"
    )
    config = GenerationConfig(wiki_style="terse")
    gen = PageGenerator(
        MockProvider(), ContextAssembler(config), config, repo_path=tmp_path
    )
    assert gen._style.name == "terse"
    ctx = gen._assembler.assemble_file_page(
        sample_parsed_file,
        sample_graph,
        graph_metrics["pagerank"],
        graph_metrics["betweenness"],
        graph_metrics["community"],
        sample_source_bytes,
    )
    rendered = gen._render("file_page.j2", ctx=ctx)
    assert "TERSE_MARKER" in rendered
