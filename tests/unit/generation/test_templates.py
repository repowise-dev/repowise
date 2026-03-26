"""Tests for Jinja2 templates — 27 tests (3 per template × 9 templates)."""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

from repowise.core.generation.context_assembler import (
    ApiContractContext,
    ArchitectureDiagramContext,
    DiffSummaryContext,
    FilePageContext,
    InfraPageContext,
    ModulePageContext,
    SccPageContext,
    SymbolSpotlightContext,
    RepoOverviewContext,
    _TopFile,
)
from repowise.core.ingestion.models import PackageInfo


# ---------------------------------------------------------------------------
# Fixture: Jinja2 environment pointing at the real templates directory
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def jinja_env() -> jinja2.Environment:
    templates_dir = Path(__file__).parents[3] / "packages" / "core" / "src" / "repowise" / "core" / "generation" / "templates"
    assert templates_dir.exists(), f"Templates directory not found: {templates_dir}"
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates_dir)),
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )


def render(env: jinja2.Environment, template_name: str, ctx: object) -> str:
    return env.get_template(template_name).render(ctx=ctx)


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
            {"name": "Calculator", "kind": "class", "signature": "class Calculator:", "docstring": "Calc.", "visibility": "public", "is_async": False, "complexity_estimate": 1, "decorators": [], "parent_name": None, "start_line": 1, "end_line": 10}
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
        module_path="python_pkg",
        language="python",
        total_symbols=5,
        public_symbols=3,
        entry_points=["python_pkg/__init__.py"],
        dependencies=["external_lib"],
        dependents=[],
        pagerank_mean=0.3,
        files=["python_pkg/calculator.py", "python_pkg/models.py"],
    )


def test_module_page_renders_without_error(jinja_env, module_page_ctx):
    result = render(jinja_env, "module_page.j2", module_page_ctx)
    assert result


def test_module_page_has_heading(jinja_env, module_page_ctx):
    result = render(jinja_env, "module_page.j2", module_page_ctx)
    assert "##" in result


def test_module_page_contains_module_path(jinja_env, module_page_ctx):
    result = render(jinja_env, "module_page.j2", module_page_ctx)
    assert module_page_ctx.module_path in result


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
    result = render(jinja_env, "scc_page.j2", scc_page_ctx)
    assert result


def test_scc_page_has_heading(jinja_env, scc_page_ctx):
    result = render(jinja_env, "scc_page.j2", scc_page_ctx)
    assert "##" in result


def test_scc_page_contains_cycle_description(jinja_env, scc_page_ctx):
    result = render(jinja_env, "scc_page.j2", scc_page_ctx)
    assert scc_page_ctx.cycle_description in result


# ---------------------------------------------------------------------------
# diff_summary.j2
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def diff_summary_ctx() -> DiffSummaryContext:
    return DiffSummaryContext(
        from_ref="HEAD~1",
        to_ref="HEAD",
        added_files=["new_file.py"],
        deleted_files=[],
        modified_files=["changed.py"],
        symbol_diffs=[],
        affected_page_ids=["file_page:changed.py"],
    )


def test_diff_summary_renders_without_error(jinja_env, diff_summary_ctx):
    result = render(jinja_env, "diff_summary.j2", diff_summary_ctx)
    assert result


def test_diff_summary_has_heading(jinja_env, diff_summary_ctx):
    result = render(jinja_env, "diff_summary.j2", diff_summary_ctx)
    assert "##" in result


def test_diff_summary_contains_refs(jinja_env, diff_summary_ctx):
    result = render(jinja_env, "diff_summary.j2", diff_summary_ctx)
    assert diff_summary_ctx.from_ref in result
    assert diff_summary_ctx.to_ref in result
