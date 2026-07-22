"""Unit tests for the ``repowise generate`` scope/cascade resolution."""

from __future__ import annotations

from dataclasses import dataclass

from repowise.cli.commands.generate_cmd.scope import (
    _ranked_ids_to_seed,
    build_cost_plans,
    load_page_records,
    resolve_scope,
    selection_page_ids,
)
from repowise.core.generation.cascade import build_page_dependencies
from repowise.core.generation.models import compute_page_id
from repowise.core.generation.page_selection import PageRecord, PageSelectionIntent


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


class _FakeGroup:
    """Duck-typed selection ``ModuleGroup``."""

    def __init__(self, key: str) -> None:
        self.key = key


class _FakeSelection:
    """Minimal duck-typed :class:`Selection` for ``selection_page_ids``."""

    def __init__(self) -> None:
        self.file_page_paths = ["src/a.py", "src/b.py"]
        self.module_groups = [_FakeGroup("src")]
        self.scc_groups = [("cycle-1", ["src/a.py", "src/b.py"])]
        self.api_contract_paths = ["openapi.yaml"]
        self.infra_paths = ["Dockerfile"]
        self.symbol_spotlights = [("src/a.py", "Widget")]
        self.emit_repo_overview = True
        self.emit_arch_diagram = True


def test_selection_page_ids_mirrors_generation_id_assignment() -> None:
    ids = selection_page_ids(_FakeSelection(), "demo")
    assert ids == {
        compute_page_id("file_page", "src/a.py"),
        compute_page_id("file_page", "src/b.py"),
        compute_page_id("module_page", "src"),
        compute_page_id("scc_page", "cycle-1"),
        compute_page_id("api_contract", "openapi.yaml"),
        compute_page_id("infra_page", "Dockerfile"),
        compute_page_id("symbol_spotlight", "src/a.py::Widget"),
        compute_page_id("repo_overview", "demo"),
        compute_page_id("architecture_diagram", "demo"),
    }


def test_selection_page_ids_honors_emit_flags() -> None:
    sel = _FakeSelection()
    sel.emit_repo_overview = False
    sel.emit_arch_diagram = False
    ids = selection_page_ids(sel, "demo")
    assert compute_page_id("repo_overview", "demo") not in ids
    assert compute_page_id("architecture_diagram", "demo") not in ids


def test_ranked_ids_to_seed_keeps_unwritten_and_adds_structural() -> None:
    records = [
        PageRecord("file_page:a.py", "file_page", "a.py", is_template=True),
        PageRecord("file_page:b.py", "file_page", "b.py", is_template=False),
        PageRecord("layer_page:core", "layer_page", "core", is_template=True),
        PageRecord("onboarding:tour", "onboarding", "tour", is_template=True),
        PageRecord("onboarding:map", "onboarding", "map", is_template=False),
    ]
    # Ranked picks both file pages; the written one (b.py) must drop, and the
    # unwritten structural pages (layer + one onboarding) must be pulled in even
    # though the ranked set never named them.
    seed = _ranked_ids_to_seed({"file_page:a.py", "file_page:b.py"}, records)
    assert seed == {"file_page:a.py", "layer_page:core", "onboarding:tour"}


def test_ranked_ids_to_seed_drops_ids_with_no_page() -> None:
    # A ranked id for a file added since indexing has no page row; it is dropped
    # rather than fabricated (generate only rewrites existing pages).
    records = [PageRecord("file_page:a.py", "file_page", "a.py", is_template=True)]
    seed = _ranked_ids_to_seed({"file_page:a.py", "file_page:new.py"}, records)
    assert seed == {"file_page:a.py"}


def test_resolve_scope_uses_ranked_seed_verbatim() -> None:
    records = [
        PageRecord("file_page:a.py", "file_page", "a.py", is_template=True),
        PageRecord("file_page:b.py", "file_page", "b.py", is_template=True),
    ]
    deps = build_page_dependencies(
        module_groups=[], scc_groups=[], layer_page_of={}, repo_wide_ids=()
    )
    # An all-selecting intent would pick both; the ranked seed overrides it.
    plan = resolve_scope(
        records=records,
        intent=PageSelectionIntent(all_pages=True),
        cascade_mode="none",
        deps=deps,
        ranked_seed={"file_page:a.py"},
    )
    assert plan.generate_ids == {"file_page:a.py"}
    assert plan.seed_count == 1
    assert plan.unknown_page_ids == ()
