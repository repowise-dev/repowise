"""Tests that every shipped Jinja template renders against a real context."""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

from repowise.core.generation.context_assembler import (
    ApiContractContext,
    ArchitectureDiagramContext,
    FilePageContext,
    InfraPageContext,
    ModulePageContext,
    RepoOverviewContext,
    SccPageContext,
    SymbolSpotlightContext,
    _TopFile,
)
from repowise.core.generation.page_generator.structural import (
    as_markdown,
    oneline,
    signature,
)
from repowise.core.ingestion.models import PackageInfo

# ---------------------------------------------------------------------------
# Fixture: Jinja2 environment pointing at the real templates directory
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def jinja_env() -> jinja2.Environment:
    templates_dir = (
        Path(__file__).parents[3]
        / "packages"
        / "core"
        / "src"
        / "repowise"
        / "core"
        / "generation"
        / "templates"
    )
    assert templates_dir.exists(), f"Templates directory not found: {templates_dir}"
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates_dir)),
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )
    # The same filters PageGenerator registers. Structural templates render
    # page content rather than a prompt, so they lean on these to keep
    # docstrings and signatures inside a table cell or list item.
    env.filters.setdefault("oneline", oneline)
    env.filters.setdefault("as_markdown", as_markdown)
    env.filters.setdefault("signature", signature)
    return env


def render(env: jinja2.Environment, template_name: str, ctx: object, **extra: object) -> str:
    return env.get_template(template_name).render(ctx=ctx, **extra)


# ---------------------------------------------------------------------------
# file_page.j2
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def file_page_ctx() -> FilePageContext:
    return FilePageContext(
        file_path="python_pkg/calculator.py",
        language="python",
        docstring="Calculator module.",
        symbols=[
            {
                "name": "Calculator",
                "kind": "class",
                "signature": "class Calculator:",
                "docstring": "Calc.",
                "visibility": "public",
                "is_async": False,
                "complexity_estimate": 1,
                "decorators": [],
                "parent_name": None,
                "start_line": 1,
                "end_line": 10,
            }
        ],
        imports=["from python_pkg import models"],
        exports=["Calculator"],
        file_source_snippet="class Calculator:\n    pass",
        pagerank_score=0.5,
        betweenness_score=0.2,
        community_id=0,
        dependents=["main.py"],
        dependencies=["python_pkg/models.py"],
        is_api_contract=False,
        is_entry_point=False,
        is_test=False,
        parse_errors=[],
        estimated_tokens=100,
    )


def test_file_page_renders_without_error(jinja_env, file_page_ctx):
    result = render(jinja_env, "file_page.j2", file_page_ctx)
    assert result  # non-empty


def test_file_page_has_heading(jinja_env, file_page_ctx):
    result = render(jinja_env, "file_page.j2", file_page_ctx)
    assert "##" in result


def test_file_page_contains_file_path(jinja_env, file_page_ctx):
    result = render(jinja_env, "file_page.j2", file_page_ctx)
    assert file_page_ctx.file_path in result


@pytest.fixture(scope="module")
def bare_file_page_ctx() -> FilePageContext:
    """A file the graph knows nothing about: no symbols, no edges, no docstring.

    Config files, data files and the unparsed tail of a large repo all land
    here, so this is not a corner case — it is a large share of the file pages
    a real wiki contains.
    """
    return FilePageContext(
        file_path="python_pkg/settings.py",
        language="python",
        docstring=None,
        symbols=[],
        imports=[],
        exports=[],
        file_source_snippet="DEBUG = False",
        pagerank_score=0.0,
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


def test_file_page_omits_a_section_it_cannot_fill(jinja_env, bare_file_page_ctx):
    """A heading with nothing under it is worse than no heading.

    An empty section still costs a reader a stop and still puts its words into
    the search index, where the same four sentences repeated across thousands
    of pages are noise that matches everything and distinguishes nothing.
    """
    result = render(jinja_env, "file_page.j2", bare_file_page_ctx)
    assert "## Public API" not in result
    assert "## Depends on" not in result
    assert "## Used by" not in result


def test_file_page_keeps_the_sections_it_can_fill(jinja_env, file_page_ctx):
    """The other half of the rule: a populated file still renders everything."""
    result = render(jinja_env, "file_page.j2", file_page_ctx)
    assert "## Overview" in result
    assert "## Public API" in result
    assert "## Depends on" in result
    assert "## Used by" in result


def test_file_page_leaves_no_gap_where_a_section_was_dropped(
    jinja_env, file_page_ctx, bare_file_page_ctx
):
    """Dropping a section must not leave the blank lines that framed it.

    Every section is conditional, so the run of blank lines between two of them
    depends on which ones rendered. Asserting the invariant here is what stops
    a later edit from silently reintroducing the gaps.
    """
    for ctx in (file_page_ctx, bare_file_page_ctx):
        result = render(jinja_env, "file_page.j2", ctx)
        assert "\n\n\n" not in result
        assert not result.startswith("\n")


def test_file_page_always_says_what_the_file_is(jinja_env, bare_file_page_ctx):
    """Overview stays unconditional — it is generated, so it is never empty.

    It is also what ``_extract_summary`` reads back as the page summary shown
    in search results and the wiki list, so a file page without it would be a
    blank row there.
    """
    result = render(jinja_env, "file_page.j2", bare_file_page_ctx)
    assert "## Overview" in result
    assert bare_file_page_ctx.file_path in result
    assert "_No " not in result


# ---------------------------------------------------------------------------
# module_page.j2
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def module_page_ctx() -> ModulePageContext:
    return ModulePageContext(
        title="Calculation Engine",
        language="python",
        total_symbols=5,
        public_symbols=3,
        entry_points=["python_pkg/__init__.py"],
        dependencies=["external_lib"],
        dependents=[],
        pagerank_mean=0.3,
        files=["python_pkg/calculator.py", "python_pkg/models.py"],
        directories=["python_pkg"],
    )


def test_module_page_renders_without_error(jinja_env, module_page_ctx):
    result = render(jinja_env, "module_page.j2", module_page_ctx)
    assert result


def test_module_page_has_heading(jinja_env, module_page_ctx):
    result = render(jinja_env, "module_page.j2", module_page_ctx)
    assert "##" in result


def test_module_page_names_the_group_and_where_it_lives(jinja_env, module_page_ctx):
    """The title is prose and the directories are the path. Both must appear.

    A concept page spans directories, so a reader who only gets the title has
    no way to go and look, and one who only gets a path has to guess what the
    group is for.
    """
    result = render(jinja_env, "module_page.j2", module_page_ctx)
    assert module_page_ctx.title in result
    assert "python_pkg" in result


# ---------------------------------------------------------------------------
# repo_overview.j2
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def repo_overview_ctx() -> RepoOverviewContext:
    pkg = PackageInfo(
        name="python_pkg",
        path="python_pkg",
        language="python",
        entry_points=["python_pkg/__init__.py"],
        manifest_file="pyproject.toml",
    )
    return RepoOverviewContext(
        repo_name="my-repo",
        is_monorepo=False,
        packages=[pkg],
        language_distribution={"python": 1.0},
        total_files=5,
        total_loc=200,
        entry_points=["python_pkg/__init__.py"],
        top_files_by_pagerank=[_TopFile("python_pkg/calculator.py", 0.5)],
        circular_dependency_count=0,
    )


def test_repo_overview_renders_without_error(jinja_env, repo_overview_ctx):
    result = render(jinja_env, "repo_overview.j2", repo_overview_ctx)
    assert result


def test_repo_overview_has_heading(jinja_env, repo_overview_ctx):
    result = render(jinja_env, "repo_overview.j2", repo_overview_ctx)
    assert "##" in result


def test_repo_overview_contains_repo_name(jinja_env, repo_overview_ctx):
    result = render(jinja_env, "repo_overview.j2", repo_overview_ctx)
    assert repo_overview_ctx.repo_name in result


# ---------------------------------------------------------------------------
# symbol_spotlight.j2
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def symbol_spotlight_ctx() -> SymbolSpotlightContext:
    return SymbolSpotlightContext(
        symbol_name="add",
        qualified_name="python_pkg.calculator.add",
        kind="function",
        signature="def add(a: int, b: int) -> int:",
        docstring="Add two numbers.",
        file_path="python_pkg/calculator.py",
        decorators=[],
        is_async=False,
        complexity_estimate=1,
        callers=["main.py"],
    )


def test_symbol_spotlight_renders_without_error(jinja_env, symbol_spotlight_ctx):
    result = render(jinja_env, "symbol_spotlight.j2", symbol_spotlight_ctx)
    assert result


def test_symbol_spotlight_has_heading(jinja_env, symbol_spotlight_ctx):
    result = render(jinja_env, "symbol_spotlight.j2", symbol_spotlight_ctx)
    assert "##" in result


def test_symbol_spotlight_contains_symbol_name(jinja_env, symbol_spotlight_ctx):
    result = render(jinja_env, "symbol_spotlight.j2", symbol_spotlight_ctx)
    assert symbol_spotlight_ctx.symbol_name in result


# ---------------------------------------------------------------------------
# architecture_diagram.j2
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def architecture_diagram_ctx() -> ArchitectureDiagramContext:
    return ArchitectureDiagramContext(
        repo_name="my-repo",
        nodes=["pkg/a.py", "pkg/b.py"],
        edges=[("pkg/a.py", "pkg/b.py")],
        communities={0: ["pkg/a.py"], 1: ["pkg/b.py"]},
        scc_groups=[],
    )


def test_architecture_diagram_renders_without_error(jinja_env, architecture_diagram_ctx):
    result = render(jinja_env, "architecture_diagram.j2", architecture_diagram_ctx)
    assert result


def test_architecture_diagram_has_heading(jinja_env, architecture_diagram_ctx):
    result = render(jinja_env, "architecture_diagram.j2", architecture_diagram_ctx)
    assert "##" in result


def test_architecture_diagram_mentions_mermaid(jinja_env, architecture_diagram_ctx):
    result = render(jinja_env, "architecture_diagram.j2", architecture_diagram_ctx)
    assert "mermaid" in result.lower()


# ---------------------------------------------------------------------------
# api_contract.j2
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_contract_ctx() -> ApiContractContext:
    return ApiContractContext(
        file_path="api/openapi.yaml",
        language="openapi",
        raw_content="openapi: '3.0'\ninfo:\n  title: My API",
        endpoints=["/users GET", "/users POST"],
        schemas=["User", "Error"],
    )


def test_api_contract_renders_without_error(jinja_env, api_contract_ctx):
    result = render(jinja_env, "api_contract.j2", api_contract_ctx)
    assert result


def test_api_contract_has_heading(jinja_env, api_contract_ctx):
    result = render(jinja_env, "api_contract.j2", api_contract_ctx)
    assert "##" in result


def test_api_contract_contains_file_path(jinja_env, api_contract_ctx):
    result = render(jinja_env, "api_contract.j2", api_contract_ctx)
    assert api_contract_ctx.file_path in result


# ---------------------------------------------------------------------------
# infra_page.j2
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def infra_page_ctx() -> InfraPageContext:
    return InfraPageContext(
        file_path="Dockerfile",
        language="dockerfile",
        raw_content="FROM ubuntu:22.04\nRUN apt-get update",
        targets=["build", "test"],
    )


def test_infra_page_renders_without_error(jinja_env, infra_page_ctx):
    result = render(jinja_env, "infra_page.j2", infra_page_ctx)
    assert result


def test_infra_page_has_heading(jinja_env, infra_page_ctx):
    result = render(jinja_env, "infra_page.j2", infra_page_ctx)
    assert "##" in result


def test_infra_page_contains_file_path(jinja_env, infra_page_ctx):
    result = render(jinja_env, "infra_page.j2", infra_page_ctx)
    assert infra_page_ctx.file_path in result


# ---------------------------------------------------------------------------
# scc_page.j2
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scc_page_ctx() -> SccPageContext:
    return SccPageContext(
        scc_id="scc-0",
        files=["pkg/a.py", "pkg/b.py"],
        cycle_description="Circular dependency cycle: pkg/a.py → pkg/b.py",
        total_symbols=10,
    )


def test_scc_page_renders_without_error(jinja_env, scc_page_ctx):
    result = render(jinja_env, "scc_page.j2", scc_page_ctx, decouple_ranking=[])
    assert result


def test_scc_page_has_heading(jinja_env, scc_page_ctx):
    result = render(jinja_env, "scc_page.j2", scc_page_ctx, decouple_ranking=[])
    assert "##" in result


def test_scc_page_contains_cycle_description(jinja_env, scc_page_ctx):
    result = render(jinja_env, "scc_page.j2", scc_page_ctx, decouple_ranking=[])
    # The structural page writes its own sentence about the cycle rather than
    # echoing the assembled description, so assert on the members it names.
    for member in scc_page_ctx.files:
        assert member in result
