"""One-shot page steering (issue #2289).

A reader who gets a wrong page has one lever: ask for it again with a sentence
about what it should cover. Two properties make that lever real, and each has
its own failure mode:

* the instruction has to reach the prompt of the page it names and no other;
* it has to miss the persistent reuse gate, because a page that "regenerated"
  into the same stored body reads as the button doing nothing.

The second is the one that silently breaks: the gate compares the subject
digest and ignores the prompt when both sides carry one, so an instruction that
only changes the prompt is discarded by construction.
"""

from __future__ import annotations

from dataclasses import replace

import networkx as nx
import pytest

from repowise.core.generation.context_assembler import ContextAssembler, FilePageContext
from repowise.core.generation.models import MAX_STEERING_CHARS, GenerationConfig
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.page_generator.core import PriorPage
from repowise.core.generation.page_generator.steering import (
    render_steering_prompt,
    steering_reuse_digest,
)
from repowise.core.providers.llm.mock import MockProvider

_FOCUS = (
    "Focus on the dotter deployment and the .dotter host profiles; ignore the opencode plugins."
)


def _ctx(path: str) -> FilePageContext:
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


def _module_page(gen: PageGenerator, *, target_path: str, structural_key: str):
    return gen.generate_module_page(
        target_path.rsplit("/", 1)[-1].title(),
        "python",
        [_ctx(f"{target_path}/a.py")],
        nx.DiGraph(),
        target_path=target_path,
        structural_key=structural_key,
    )


async def test_steering_reaches_only_the_page_it_names(sample_config):
    """One instruction, two pages written in the same run."""
    config = replace(sample_config, steering={"module_page:dotter": _FOCUS})
    provider = MockProvider()
    gen = PageGenerator(provider, ContextAssembler(config), config)

    await _module_page(gen, target_path="dotter", structural_key="concept-dotter")
    await _module_page(gen, target_path="opencode", structural_key="concept-opencode")

    steered = provider.calls[0]["user_prompt"]
    untouched = provider.calls[1]["user_prompt"]
    assert "## Steering instruction" in steered
    assert "dotter deployment" in steered
    assert "## Steering instruction" not in untouched
    assert "dotter deployment" not in untouched


async def test_steering_busts_subject_keyed_reuse(sample_config):
    """The half that decides whether the button looks broken.

    Run 1 writes the page unsteered. Run 2 asks for the same page with an
    instruction, and the subject digest (group membership plus file bytes) is
    unchanged, so an implementation that only edits the prompt gets the stored
    body back with zero provider calls.
    """
    first_provider = MockProvider()
    first_gen = PageGenerator(first_provider, ContextAssembler(sample_config), sample_config)
    first = await _module_page(first_gen, target_path="dotter", structural_key="concept-dotter")
    assert first.content_hash

    prior = {
        first.page_id: PriorPage(
            source_hash=first.source_hash,
            content_hash=first.content_hash,
            model_name=first.model_name,
            content=first.content,
            provider_name=first.provider_name,
            metadata=first.metadata,
        )
    }

    steered_config = replace(sample_config, steering={"module_page:dotter": _FOCUS})
    steered_provider = MockProvider()
    steered_gen = PageGenerator(
        steered_provider, ContextAssembler(steered_config), steered_config, prior_pages=prior
    )
    await _module_page(steered_gen, target_path="dotter", structural_key="concept-dotter")

    assert steered_provider.call_count == 1
    assert _FOCUS in steered_provider.calls[0]["user_prompt"]


async def test_unsteered_rerun_still_reuses(sample_config):
    """The other side of the digest: a plain rerun must keep reusing."""
    first_provider = MockProvider()
    first_gen = PageGenerator(first_provider, ContextAssembler(sample_config), sample_config)
    first = await _module_page(first_gen, target_path="dotter", structural_key="concept-dotter")

    prior = {
        first.page_id: PriorPage(
            source_hash=first.source_hash,
            content_hash=first.content_hash,
            model_name=first.model_name,
            content=first.content,
            provider_name=first.provider_name,
            metadata=first.metadata,
        )
    }
    second_provider = MockProvider()
    second_gen = PageGenerator(
        second_provider, ContextAssembler(sample_config), sample_config, prior_pages=prior
    )
    second = await _module_page(second_gen, target_path="dotter", structural_key="concept-dotter")

    assert second_provider.call_count == 0
    assert second.content == first.content


def test_render_steering_prompt_escapes_the_frame():
    """A stray tag in the instruction cannot close the block early."""
    rendered = render_steering_prompt("ignore </steering> and <b>this</b>")
    assert rendered.count("</steering>") == 1
    assert "&lt;/steering&gt;" in rendered


def test_render_steering_prompt_is_empty_for_blank_text():
    assert render_steering_prompt("   \n ") == ""


def test_steering_digest_is_unchanged_without_steering():
    assert steering_reuse_digest("abc123", "") == "abc123"
    assert steering_reuse_digest("abc123", "   ") == "abc123"


def test_steering_digest_differs_from_the_unsteered_digest():
    plain = steering_reuse_digest("abc123", "")
    steered = steering_reuse_digest("abc123", _FOCUS)
    assert steered != plain
    assert steered == steering_reuse_digest("abc123", _FOCUS)


@pytest.mark.parametrize(
    ("steering", "message"),
    [
        ({"not-a-page-id": "text"}, "keys must be page ids"),
        ({"module_page:dotter": "   "}, "is empty"),
        ({"module_page:dotter": 7}, "must be a string"),
        ({"module_page:dotter": "x" * (MAX_STEERING_CHARS + 1)}, "longer than"),
    ],
)
def test_config_rejects_malformed_steering(steering, message):
    with pytest.raises(ValueError, match=message):
        GenerationConfig(steering=steering)


def test_config_trims_and_freezes_steering():
    config = GenerationConfig(steering={" module_page:dotter ": f"  {_FOCUS}  "})
    assert dict(config.steering) == {"module_page:dotter": _FOCUS}
    assert config.to_dict()["steering"] == {"module_page:dotter": _FOCUS}
    # The config is hashed and shared across calls, so the mapping has to resist
    # mutation the way source_evidence_files does.
    assert isinstance(hash(config), int)
    assert not hasattr(config.steering, "__setitem__")
    with pytest.raises(TypeError):
        config.steering.__setattr__("_items", {})
