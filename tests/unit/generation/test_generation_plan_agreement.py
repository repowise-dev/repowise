"""The generation plan promises the pages generation writes, page type by page type.

The plan used to count every concept group as a subsystem page and every
onboarding slot as a model page; generation skips a group with nothing to
write about and a slot whose gate fails, so a small repo was quoted for pages
it never got.
"""

from __future__ import annotations

from collections import Counter

from repowise.core.cost_estimator import build_generation_plan, estimate_cost
from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.onboarding import iter_specs
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import ParsedFile, RepoStructure
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
        # More files than one concept group holds, so each directory gets its own.
        *(_parsed(f"pkg/m{i}.py", [f"route{i}", f"dispatch{i}"]) for i in range(10)),
        *(_parsed(f"lib/u{i}.py", [f"slug{i}"]) for i in range(8)),
        # Define and import nothing: their group has nothing to write about.
        _parsed("tools/release.py", []),
        _parsed("tools/bump.py", []),
    ]


def _builder(parsed: list[ParsedFile]) -> GraphBuilder:
    builder = GraphBuilder()
    for p in parsed:
        builder.add_file(p)
    builder.build()
    return builder


async def test_the_plan_counts_the_pages_generation_writes() -> None:
    parsed = _repo_files()
    builder = _builder(parsed)
    config = GenerationConfig(
        max_tokens=256, token_budget=500, max_concurrency=2, enable_onboarding=False
    )
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
        parsed, {p.file_info.path: b"x = 1\n" for p in parsed}, builder, repo, "r"
    )

    written = Counter(p.page_type for p in pages)
    planned = {p.page_type: p.count for p in build_generation_plan(parsed, builder, config)}
    # The empty group is selected but gets no page; the plan says so too.
    assert len(gen.selection.module_groups) > written["module_page"]
    assert planned == dict(written)


def test_a_deterministic_onboarding_slot_whose_gate_fails_is_not_planned() -> None:
    """With no git activity the active-landscape slot is skipped, and it never cost tokens."""
    parsed = _repo_files()
    builder = _builder(parsed)
    plans = build_generation_plan(parsed, builder, GenerationConfig())
    onboarding = next(p for p in plans if p.page_type == "onboarding")
    model_slots = sum(1 for spec in iter_specs() if not spec.deterministic)

    assert onboarding.count == model_slots
    assert onboarding.free == 0


def test_free_onboarding_pages_are_not_priced() -> None:
    parsed = _repo_files()
    plans = build_generation_plan(parsed, _builder(parsed), GenerationConfig())
    onboarding = next(p for p in plans if p.page_type == "onboarding")
    priced = estimate_cost(plans, "openai", "gpt-5.6-luna").estimated_cost_usd

    onboarding.free = onboarding.count
    assert estimate_cost(plans, "openai", "gpt-5.6-luna").estimated_cost_usd < priced
