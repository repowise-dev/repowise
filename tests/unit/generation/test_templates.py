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


@pytest.fixture(scope="module")
def unimported_spotlight_ctx() -> SymbolSpotlightContext:
    """A symbol nothing in the repository imports, and with no docstring.

    Both halves matter: the importer list is what the dropped section was
    reporting on, and the missing docstring is what the Overview has to cover
    for on its own.
    """
    return SymbolSpotlightContext(
        symbol_name="_normalise",
        qualified_name="python_pkg.calculator._normalise",
        kind="function",
        signature="def _normalise(value: str) -> str:",
        docstring=None,
        file_path="python_pkg/calculator.py",
        decorators=[],
        is_async=False,
        complexity_estimate=1,
        callers=[],
    )


def test_spotlight_omits_the_importers_section_when_nothing_imports_it(
    jinja_env, unimported_spotlight_ctx
):
    """No importers is not a fact worth a heading of its own."""
    result = render(jinja_env, "symbol_spotlight.j2", unimported_spotlight_ctx)
    assert "## Where it is used" not in result
    assert "No importers" not in result


def test_spotlight_keeps_the_importers_section_when_it_has_importers(
    jinja_env, symbol_spotlight_ctx
):
    """The other half: a symbol with importers still lists them, still hedged.

    The hedge is load-bearing. ``callers`` holds files importing the defining
    module, not verified call sites, and the sentence has to keep saying so.
    """
    result = render(jinja_env, "symbol_spotlight.j2", symbol_spotlight_ctx)
    assert "## Where it is used" in result
    assert "`main.py`" in result
    assert "not confirmed call sites" in result
    # The count and its verb have to agree; one importer is the common case.
    assert "1 file imports the module" in result


def test_spotlight_still_describes_a_symbol_that_has_no_docstring(
    jinja_env, unimported_spotlight_ctx
):
    """The Overview sentence stays even though it reads like a placeholder.

    It is the page summary — ``_extract_summary`` skips the metadata line and
    the signature fence and lands on it — and it names the symbol, its kind and
    its file, which is the identifier-bearing text the page exists to carry.
    Deleting it would blank the summary for every undocumented symbol.
    """
    result = render(jinja_env, "symbol_spotlight.j2", unimported_spotlight_ctx)
    assert "## Overview" in result
    assert "_normalise" in result
    assert "python_pkg/calculator.py" in result


def test_spotlight_leaves_no_gap_where_a_section_was_dropped(
    jinja_env, symbol_spotlight_ctx, unimported_spotlight_ctx
):
    for ctx in (symbol_spotlight_ctx, unimported_spotlight_ctx):
        result = render(jinja_env, "symbol_spotlight.j2", ctx)
        assert "\n\n\n" not in result
        assert not result.startswith("\n")


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
