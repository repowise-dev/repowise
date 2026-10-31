"""Generation, its plan and the file-page scope agree on which files get a page.

A fresh index used to report ``partial`` because the scope counted files
selection drops by design (symbol-less barrels, near-clones) as missing pages.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.cost_estimator import build_generation_plan
from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.selection.selector import eligible_file_page_paths
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import ParsedFile, RepoStructure
from repowise.core.pipeline.phases.generation import _selected_file_pages_written
from repowise.core.providers.llm.mock import MockProvider

from .conftest import _make_file_info, _make_symbol


def _parsed(path: str, symbols: list[str]) -> ParsedFile:
    return ParsedFile(
        file_info=_make_file_info(path),
        symbols=[_make_symbol(name=name, file_path=path) for name in symbols],
        imports=[],
        exports=[],
        docstring=None,
        parse_errors=[],
    )


def _repo_files() -> list[ParsedFile]:
    return [
        _parsed("pkg/main.py", ["main", "helper"]),
        # Three files of the same shape in one folder: one keeps a page.
        _parsed("pkg/adapters/a.py", ["handle"]),
        _parsed("pkg/adapters/b.py", ["handle"]),
        _parsed("pkg/adapters/c.py", ["handle"]),
        # No symbols and no entry or hotspot signal: never selected.
        _parsed("pkg/barrel.py", []),
    ]


async def test_generation_plan_and_scope_count_the_same_file_pages() -> None:
    parsed = _repo_files()
    builder = GraphBuilder()
    for p in parsed:
        builder.add_file(p)
    builder.build()
    config = GenerationConfig(max_tokens=256, token_budget=500, max_concurrency=2)
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    repo = RepoStructure(
        is_monorepo=False,
        packages=[],
        root_language_distribution={"python": 1.0},
        total_files=len(parsed),
        total_loc=50,
        entry_points=[],
    )

    pages = await gen.generate_all(
        parsed, {p.file_info.path: b"def f(): pass" for p in parsed}, builder, repo, "r"
    )

    eligible = eligible_file_page_paths(parsed, builder.pagerank(), hotspots=set())
    written = {p.target_path for p in pages if p.page_type == "file_page"}
    planned = {
        plan.page_type: plan.count for plan in build_generation_plan(parsed, builder, config)
    }
    assert len(eligible) == 2
    assert written == eligible
    assert gen.selection.eligible_file_pages == len(eligible)
    assert planned["file_page"] == len(eligible)
    assert _selected_file_pages_written(gen.selection, pages, None) == len(eligible)


def test_a_selected_file_without_a_page_is_still_a_gap() -> None:
    selection = SimpleNamespace(file_page_paths=["a.py", "b.py", "c.py"])
    pages = [SimpleNamespace(page_type="file_page", target_path="a.py")]

    assert _selected_file_pages_written(selection, pages, None) == 1
    # A resumed run keeps the page an earlier run wrote.
    assert _selected_file_pages_written(selection, pages, {"file_page:b.py"}) == 2
