"""A module page keeps agent material in its digest, off the body a reader reads.

The questions a page answers, its identifiers and its git signals help search
and agents; a reader scrolled past them. They now live in ``page.digest``,
which is indexed and served beside the body, so the body can be prose.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.generation.agent_digest import (
    MODULE_SIGNALS_KEY,
    rejoin_questions,
    split_questions,
)
from repowise.core.generation.context_assembler import ContextAssembler, FilePageContext
from repowise.core.generation.models import GenerationConfig, compute_page_id
from repowise.core.generation.page_generator import PageGenerator, PriorPage
from repowise.core.providers.llm.mock import MockProvider

_BODY = "# Parsing\n\nThe parser turns files into records.\n\n## How a file is read\n\nIt reads.\n"
_QUESTIONS = "## Questions this page answers\n\n- How is a file parsed?\n- Where do records go?"


class _Provider(MockProvider):
    async def generate(self, *args, **kwargs):
        response = await super().generate(*args, **kwargs)
        response.content = f"{_BODY}\n{_QUESTIONS}\n"
        return response


def _file_context(path: str) -> FilePageContext:
    return FilePageContext(
        file_path=path,
        language="python",
        docstring=None,
        symbols=[
            {
                "name": "Parser",
                "qualified_name": "Parser",
                "kind": "class",
                "signature": "class Parser",
                "docstring": "",
                "visibility": "public",
                "is_async": False,
                "complexity_estimate": 1,
                "decorators": [],
                "parent_name": "",
                "start_line": 1,
                "end_line": 9,
            }
        ],
        imports=[],
        exports=["Parser"],
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


_GIT = {
    "pkg/parser.py": {
        "file_path": "pkg/parser.py",
        "is_hotspot": True,
        "bus_factor": 1,
        "prior_defect_count": 3,
        "primary_owner_name": "Ada",
        "commit_count_90d": 7,
    }
}


def _generator(config: GenerationConfig, **kwargs) -> PageGenerator:
    return PageGenerator(_Provider(), ContextAssembler(config), config, **kwargs)


async def _module_page(gen: PageGenerator, git_meta_map=None):
    return await gen.generate_module_page(
        "Parsing",
        "python",
        [_file_context("pkg/parser.py")],
        nx.DiGraph(),
        git_meta_map=git_meta_map,
        target_path="pkg",
        structural_key="k",
    )


def test_split_moves_the_questions_and_keeps_the_body():
    body, questions = split_questions(f"{_BODY}\n{_QUESTIONS}\n\n## After\n\nMore prose.\n")

    assert questions == _QUESTIONS
    assert "Questions this page answers" not in body
    assert "## How a file is read" in body and "## After" in body


def test_split_ignores_a_heading_inside_a_code_fence():
    content = "# T\n\n```md\n## Questions this page answers\n```\n"

    assert split_questions(content) == (content, "")


def test_rejoin_restores_the_response_a_body_was_split_from():
    body, questions = split_questions(f"{_BODY}\n{_QUESTIONS}\n")

    assert split_questions(rejoin_questions(body, questions)) == (body, questions)
    assert rejoin_questions(body, "") == body


async def test_the_model_questions_move_into_the_digest(sample_config):
    page = await _module_page(_generator(sample_config))

    assert "Questions this page answers" not in page.content
    assert page.digest.startswith(_QUESTIONS)
    assert "## Concept index" in page.digest
    assert page.summary.startswith("The parser turns files into records")


async def test_git_signals_reach_the_digest_and_the_card_not_the_body(sample_config):
    page = await _module_page(_generator(sample_config), git_meta_map=_GIT)

    signals = page.metadata[MODULE_SIGNALS_KEY]
    assert signals["files"] == 1
    assert signals["hotspots"] == 1
    assert signals["bus_factor_one"] == 1
    assert signals["bug_fixes"] == 3
    assert signals["owners"] == [{"name": "Ada", "files": 1}]
    assert "## Signals" in page.digest
    assert "Bug-fix commits: 3, most in `pkg/parser.py`" in page.digest
    assert "hotspot" not in page.content.lower()


async def test_a_module_with_no_history_reports_no_signals(sample_config):
    page = await _module_page(_generator(sample_config))

    assert MODULE_SIGNALS_KEY not in page.metadata
    assert "## Signals" not in page.digest


async def test_the_keyless_page_splits_the_same_way():
    config = GenerationConfig(
        max_tokens=1024, token_budget=2000, max_concurrency=2, deterministic=True
    )
    page = await _module_page(_generator(config), git_meta_map=_GIT)

    assert "Questions this page answers" not in page.content
    assert page.digest.startswith("## Questions this page answers")
    assert "Subsystem health" not in page.content
    assert page.metadata[MODULE_SIGNALS_KEY]["hotspots"] == 1


async def test_a_page_reused_from_a_prior_run_keeps_its_questions(sample_config):
    first = await _module_page(_generator(sample_config))
    prior = {
        compute_page_id("module_page", "pkg"): PriorPage(
            source_hash=first.source_hash,
            model_name=first.model_name,
            content=first.content,
            digest=first.digest,
        )
    }

    again = await _module_page(_generator(sample_config, prior_pages=prior))

    assert again.metadata.get("reused_from_prior_run") is True
    assert again.content == first.content
    assert again.digest == first.digest


async def test_a_reused_rollup_keeps_one_package_table(sample_config):
    async def rollup(gen):
        return await gen.generate_module_page(
            "Parsing",
            "python",
            [_file_context("pkg/parser.py")],
            nx.DiGraph(),
            target_path="pkg",
            structural_key="k",
            packages=[{"path": "pkg/a", "files": 1}, {"path": "pkg/b", "files": 2}],
        )

    page = await rollup(_generator(sample_config))
    for _ in range(2):
        prior = {
            page.page_id: PriorPage(
                source_hash=page.source_hash,
                model_name=page.model_name,
                content=page.content,
                digest=page.digest,
            )
        }
        page = await rollup(_generator(sample_config, prior_pages=prior))

    assert page.metadata.get("reused_from_prior_run") is True
    assert page.content.count("## Packages") == 1
    assert "| `pkg/b` | 2 |" in page.content
