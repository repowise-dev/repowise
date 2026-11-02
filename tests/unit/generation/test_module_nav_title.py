"""A module page names itself for the sidebar in the same call that writes it."""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.generation.context_assembler import ContextAssembler, FilePageContext
from repowise.core.generation.models import compute_page_id
from repowise.core.generation.nav_titles import (
    NAV_TITLE_KEY,
    clean_nav_title,
    page_nav_title,
    split_nav_title,
)
from repowise.core.generation.page_generator import PageGenerator, PriorPage
from repowise.core.providers.llm.mock import MockProvider

_BODY = "# Parsing\n\nThe parser turns files into records.\n"


class _Provider(MockProvider):
    reply = f"Nav: Source Parsing\n\n{_BODY}"
    calls = 0

    async def generate(self, *args, **kwargs):
        type(self).calls += 1
        response = await super().generate(*args, **kwargs)
        response.content = self.reply
        return response


def _file_context(path: str) -> FilePageContext:
    return FilePageContext(
        file_path=path,
        language="python",
        docstring=None,
        symbols=[],
        imports=[],
        exports=[],
        pagerank_score=0.3,
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


async def _module_page(config, **kwargs):
    gen = PageGenerator(_Provider(), ContextAssembler(config), config, **kwargs)
    return await gen.generate_module_page(
        "Parsing",
        "python",
        [_file_context("pkg/parser.py")],
        nx.DiGraph(),
        target_path="pkg",
        structural_key="k",
    )


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("Ingestion Pipeline", "Ingestion Pipeline"),
        ("**Mcp Server**", "MCP Server"),
        ("Dead Code Detection.", "Dead Code Detection"),
        ("packages/core/ingestion", None),
        ("Core Overview", None),
        ("The Whole Ingestion Pipeline Stage", None),
        ("", None),
        ("`parse_file`", None),
    ],
)
def test_a_chosen_name_is_checked_in_code_not_retried(raw, name):
    assert clean_nav_title(raw) == name


def test_the_nav_line_comes_off_the_body():
    assert split_nav_title(f"Nav: Parsing Engine\n\n{_BODY}") == ("Parsing Engine", _BODY)
    assert split_nav_title(_BODY) == (None, _BODY)
    assert split_nav_title(f"**Nav:** Parsing Engine\n{_BODY}") == ("Parsing Engine", _BODY)
    # A name that breaks a rule is dropped; the line still never reaches the page.
    assert split_nav_title(f"Nav: pkg/parser\n{_BODY}") == (None, _BODY)


def test_the_chosen_name_wins_over_the_rule_and_a_bad_one_falls_back():
    assert page_nav_title("module_page", "Cli Overview", {NAV_TITLE_KEY: "Command Line"}) == (
        "Command Line"
    )
    assert page_nav_title("module_page", "Cli Overview", '{"nav_title": "x/y"}') == "CLI"
    assert page_nav_title("module_page", "Cli Overview", None) == "CLI"


async def test_the_page_stores_its_name_and_keeps_its_body_clean(sample_config):
    _Provider.calls = 0
    page = await _module_page(sample_config)

    assert page.metadata[NAV_TITLE_KEY] == "Source Parsing"
    assert not page.content.startswith("Nav:")
    assert page.summary.startswith("The parser turns files into records")
    assert _Provider.calls == 1


async def test_a_reused_page_keeps_its_name_without_a_call(sample_config):
    first = await _module_page(sample_config)
    prior = {
        compute_page_id("module_page", "pkg"): PriorPage(
            source_hash=first.source_hash,
            model_name=first.model_name,
            content=first.content,
            digest=first.digest,
            metadata=dict(first.metadata),
        )
    }
    _Provider.calls = 0
    again = await _module_page(sample_config, prior_pages=prior)

    assert _Provider.calls == 0
    assert again.metadata.get("reused_from_prior_run") is True
    assert again.metadata[NAV_TITLE_KEY] == "Source Parsing"
    assert again.content == first.content
