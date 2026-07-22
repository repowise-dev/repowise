"""Unit tests for the ``repowise generate`` scope/cascade resolution."""

from __future__ import annotations

from dataclasses import dataclass

from repowise.cli.commands.generate_cmd.scope import (
    build_cost_plans,
    load_page_records,
    resolve_scope,
)
from repowise.core.generation.cascade import build_page_dependencies
from repowise.core.generation.page_selection import PageSelectionIntent


@dataclass
class _Row:
    """Duck-typed persisted Page row."""

    id: str
    page_type: str
    target_path: str
    provider_name: str
    freshness_status: str = "fresh"
    metadata_json: str = "{}"


def test_load_page_records_flags_templates_by_provider_or_metadata() -> None:
    rows = [
        _Row("file_page:a.py", "file_page", "a.py", "template"),
        _Row("file_page:b.py", "file_page", "b.py", "openai"),
        _Row(
            "file_page:c.py",
            "file_page",
            "c.py",
            "openai",
            metadata_json='{"deterministic": true}',
        ),
    ]
    records = load_page_records(rows)
    by_id = {r.page_id: r for r in records}
    assert by_id["file_page:a.py"].is_template is True
    assert by_id["file_page:b.py"].is_template is False
    # deterministic metadata still marks it template even with a real provider.
    assert by_id["file_page:c.py"].is_template is True


def test_build_cost_plans_groups_by_type_and_orders_by_level() -> None:
    ids = {
        "file_page:a.py",
        "file_page:b.py",
        "repo_overview:demo",
        "module_page:src",
    }
    plans = build_cost_plans(ids)
    counts = {p.page_type: p.count for p in plans}
    assert counts == {"file_page": 2, "module_page": 1, "repo_overview": 1}
    # Ordered by generation level (file_page=2 < module_page=4 < repo_overview=6).
    assert [p.page_type for p in plans] == ["file_page", "module_page", "repo_overview"]


def test_resolve_scope_unwritten_with_cascade_none() -> None:
    rows = [
        _Row("file_page:src/a.py", "file_page", "src/a.py", "template"),
        _Row("file_page:src/b.py", "file_page", "src/b.py", "openai"),
        _Row("module_page:src", "module_page", "src", "template"),
        _Row("repo_overview:demo", "repo_overview", "demo", "template"),
    ]
    records = load_page_records(rows)

    @dataclass(frozen=True)
    class _MG:
        key: str
        file_paths: tuple[str, ...]

    deps = build_page_dependencies(
        module_groups=[_MG("src", ("src/a.py", "src/b.py"))],
        scc_groups=[],
        layer_page_of={},
        repo_wide_ids=("repo_overview:demo",),
    )
    plan = resolve_scope(
        records=records,
        intent=PageSelectionIntent(unwritten=True),
        cascade_mode="none",
        deps=deps,
    )
    # Seeds are the template pages: a.py, module_page:src, repo_overview:demo.
    assert plan.generate_ids == {
        "file_page:src/a.py",
        "module_page:src",
        "repo_overview:demo",
    }
    # cascade=none marks the file's containers stale, minus anything regenerated.
    # module_page:src is a container of a.py but is itself regenerated, so it is
    # not stale. No repo-wide left (overview is regenerated).
    assert plan.stale_ids == set()
    assert plan.seed_count == 3
