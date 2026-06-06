"""Tests for the Phase 3 Onboarding collection.

Covers:
  - registry contents + canonical order
  - each subkind's gate (positive + negative cases)
  - `_tag_promoted_pages` on PageGenerator
  - templates render against built contexts without StrictUndefined errors
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jinja2
import pytest

from repowise.core.generation import onboarding
from repowise.core.generation.models import GENERATION_LEVELS, GeneratedPage
from repowise.core.generation.onboarding.signals import OnboardingSignals
from repowise.core.generation.onboarding.slots import (
    ONBOARDING_ORDER,
    PROMOTED_SLOTS,
    SLOT_ACTIVE_LANDSCAPE,
    SLOT_CODEBASE_MAP,
    SLOT_DEVELOPMENT_GUIDE,
    SLOT_GETTING_STARTED,
    SLOT_HOW_IT_WORKS,
    SLOT_KEY_CONCEPTS,
    target_path,
)
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.ingestion.models import (
    FileInfo,
    ParsedFile,
    RepoStructure,
    Symbol,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file(
    path: str,
    *,
    language: str = "python",
    is_entry_point: bool = False,
    symbols: list[str] | None = None,
) -> ParsedFile:
    fi = FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language=language,
        size_bytes=512,
        git_hash="abc",
        last_modified=datetime(2026, 1, 1, tzinfo=UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=is_entry_point,
    )
    syms: list[Symbol] = []
    for s in symbols or []:
        syms.append(
            Symbol(
                id=f"{path}::{s}",
                name=s,
                qualified_name=f"{path.replace('/', '.')}::{s}",
                kind="class",
                signature=f"class {s}:",
                start_line=1,
                end_line=10,
                docstring=f"Docstring for {s}.",
                decorators=[],
                visibility="public",
                is_async=False,
                complexity_estimate=1,
                language=language,
                parent_name=None,
            )
        )
    return ParsedFile(
        file_info=fi,
        symbols=syms,
        imports=[],
        exports=[s for s in (symbols or [])],
        docstring=None,
        parse_errors=[],
        content_hash="abc",
    )


def _signals(
    *,
    files: list[ParsedFile],
    pagerank: dict[str, float] | None = None,
    source_map: dict[str, bytes] | None = None,
    git_meta: dict[str, dict] | None = None,
    external_systems: tuple[dict, ...] = (),
    decisions: tuple[dict, ...] = (),
    community: dict[str, int] | None = None,
    entry_points: list[str] | None = None,
    tour_stops: tuple[dict, ...] = (),
    layer_order: tuple[str, ...] = (),
    completed_page_summaries: dict[str, str] | None = None,
) -> OnboardingSignals:
    paths = [f.file_info.path for f in files]
    pr = pagerank or {p: 0.1 for p in paths}
    com = community or dict.fromkeys(paths, 0)
    # Minimal fake graph_builder — community_info / execution_flows return empty.
    graph_builder = SimpleNamespace(
        community_info=lambda: {},
        execution_flows=lambda: SimpleNamespace(flows=[]),
    )
    repo_structure = RepoStructure(
        is_monorepo=False,
        packages=[],
        root_language_distribution={"python": 1.0},
        total_files=len(files),
        total_loc=len(files) * 50,
        entry_points=entry_points or [f.file_info.path for f in files if f.file_info.is_entry_point],
    )
    return OnboardingSignals(
        repo_name="testrepo",
        repo_structure=repo_structure,
        parsed_files=tuple(files),
        source_map=source_map or {},
        graph_builder=graph_builder,
        pagerank=pr,
        betweenness={p: 0.0 for p in paths},
        community=com,
        sccs=(),
        git_meta_map=git_meta,
        dead_code_by_file={},
        decisions_all=decisions,
        external_systems=external_systems,
        completed_page_summaries=completed_page_summaries or {},
        tour_stops=tour_stops,
        layer_order=layer_order,
    )


# ---------------------------------------------------------------------------
# Registry + ordering
# ---------------------------------------------------------------------------


def test_subkinds_registered_in_canonical_order() -> None:
    specs = onboarding.iter_specs()
    slots = [s.slot for s in specs]
    # Promoted slots are excluded from iter_specs even though they're in
    # ONBOARDING_ORDER.
    expected = [s for s in ONBOARDING_ORDER if s not in PROMOTED_SLOTS.values()]
    assert slots == expected
    # Six templated subkinds + the topology-driven guided tour.
    assert len(slots) == 7
    assert "guided_tour" in slots


def test_onboarding_level_is_eight() -> None:
    assert GENERATION_LEVELS["onboarding"] == 8


def test_target_path_format() -> None:
    assert target_path("codebase_map") == "onboarding/codebase_map"


def test_promoted_slots_map() -> None:
    assert PROMOTED_SLOTS == {
        "repo_overview": "project_overview",
        "architecture_diagram": "architecture_guide",
    }


# ---------------------------------------------------------------------------
# Codebase map — always generates
# ---------------------------------------------------------------------------


def test_codebase_map_always_builds() -> None:
    spec = onboarding.get_spec(SLOT_CODEBASE_MAP)
    assert spec is not None
    files = [
        _file("src/auth/login.py", symbols=["Login"]),
        _file("src/auth/session.py", symbols=["Session"]),
        _file("src/auth/middleware.py", symbols=["AuthMiddleware"]),
        _file("src/api/routes.py", symbols=["Router"]),
        _file("src/api/handlers.py", symbols=["Handler"]),
        _file("src/api/serializers.py", symbols=["Serializer"]),
    ]
    sig = _signals(files=files)
    ctx = spec.build_context(sig)
    assert ctx is not None
    assert len(ctx.directories) >= 1
    # Largest dir first
    assert ctx.directories[0].file_count >= ctx.directories[-1].file_count


# ---------------------------------------------------------------------------
# Active landscape — gated on git churn
# ---------------------------------------------------------------------------


def test_active_landscape_skipped_without_git_meta() -> None:
    spec = onboarding.get_spec(SLOT_ACTIVE_LANDSCAPE)
    assert spec is not None
    sig = _signals(files=[_file("src/a.py")], git_meta=None)
    assert spec.build_context(sig) is None


def test_active_landscape_skipped_below_threshold() -> None:
    spec = onboarding.get_spec(SLOT_ACTIVE_LANDSCAPE)
    assert spec is not None
    files = [_file(f"src/f{i}.py") for i in range(20)]
    # 5 files * 5 commits each = 25 commits total — below 50 floor.
    git_meta = {f.file_info.path: {"commit_count_90d": 5} for f in files[:5]}
    sig = _signals(files=files, git_meta=git_meta)
    assert spec.build_context(sig) is None


def test_active_landscape_fires_above_threshold() -> None:
    spec = onboarding.get_spec(SLOT_ACTIVE_LANDSCAPE)
    assert spec is not None
    files = [_file(f"src/f{i}.py") for i in range(20)]
    # 15 files * 10 commits = 150 commits > 50 floor, 15 files > 10 floor.
    git_meta = {
        f.file_info.path: {
            "commit_count_90d": 10,
            "is_hotspot": i < 3,
            "primary_owner_name": "alice",
            "age_days": 30,
        }
        for i, f in enumerate(files[:15])
    }
    sig = _signals(files=files, git_meta=git_meta)
    ctx = spec.build_context(sig)
    assert ctx is not None
    assert ctx.total_commits_90d == 150
    assert ctx.files_touched_90d == 15
    assert len(ctx.hot_files) <= 12
    # Top hot file should be one of the hotspots.
    assert ctx.hot_files[0].is_hotspot


# ---------------------------------------------------------------------------
# Getting started — gated on manifest OR readme section
# ---------------------------------------------------------------------------


def test_getting_started_skipped_without_signal() -> None:
    spec = onboarding.get_spec(SLOT_GETTING_STARTED)
    assert spec is not None
    sig = _signals(files=[_file("src/a.py")])
    assert spec.build_context(sig) is None


def test_getting_started_fires_on_manifest() -> None:
    spec = onboarding.get_spec(SLOT_GETTING_STARTED)
    assert spec is not None
    sig = _signals(
        files=[_file("src/a.py")],
        external_systems=(
            {"name": "fastapi", "ecosystem": "pypi", "category": "framework"},
            {"name": "pytest", "ecosystem": "pypi", "is_dev": True},
        ),
    )
    ctx = spec.build_context(sig)
    assert ctx is not None
    assert "pypi" in ctx.package_managers
    assert any(d["name"] == "fastapi" for d in ctx.runtime_dependencies)
    assert any(d["name"] == "pytest" for d in ctx.dev_dependencies)


def test_getting_started_fires_on_readme_install_section() -> None:
    spec = onboarding.get_spec(SLOT_GETTING_STARTED)
    assert spec is not None
    readme = b"""# Cool Project

## Installation

Run `pip install -e .`.

## Running

`python -m cool`.
"""
    sig = _signals(files=[_file("src/a.py")], source_map={"README.md": readme})
    ctx = spec.build_context(sig)
    assert ctx is not None
    headings = {s.heading for s in ctx.readme_sections}
    assert "Install" in headings


# ---------------------------------------------------------------------------
# Key concepts — gated on ≥4 high-PageRank public symbols
# ---------------------------------------------------------------------------


def test_key_concepts_skipped_when_too_few_concepts() -> None:
    spec = onboarding.get_spec(SLOT_KEY_CONCEPTS)
    assert spec is not None
    sig = _signals(files=[_file("src/a.py", symbols=["A"])])
    assert spec.build_context(sig) is None


def test_key_concepts_fires_with_enough_concepts() -> None:
    spec = onboarding.get_spec(SLOT_KEY_CONCEPTS)
    assert spec is not None
    files = [
        _file("src/handler.py", symbols=["Handler"]),
        _file("src/router.py", symbols=["Router"]),
        _file("src/middleware.py", symbols=["Middleware"]),
        _file("src/session.py", symbols=["Session"]),
        _file("src/util.py", symbols=["Util"]),
    ]
    # Boost the first four files into the top decile.
    pagerank = {
        "src/handler.py": 0.9,
        "src/router.py": 0.8,
        "src/middleware.py": 0.7,
        "src/session.py": 0.6,
        "src/util.py": 0.05,
    }
    sig = _signals(files=files, pagerank=pagerank)
    ctx = spec.build_context(sig)
    assert ctx is not None
    assert len(ctx.concept_symbols) >= 4
    names = {c.name for c in ctx.concept_symbols}
    assert {"Handler", "Router", "Middleware", "Session"}.issubset(names)


# ---------------------------------------------------------------------------
# How it works — gated on flow OR archetype
# ---------------------------------------------------------------------------


def test_how_it_works_skipped_for_flat_module_collection() -> None:
    spec = onboarding.get_spec(SLOT_HOW_IT_WORKS)
    assert spec is not None
    sig = _signals(files=[_file("src/util.py"), _file("src/helper.py")])
    assert spec.build_context(sig) is None


def test_how_it_works_fires_on_service_archetype() -> None:
    spec = onboarding.get_spec(SLOT_HOW_IT_WORKS)
    assert spec is not None
    sig = _signals(
        files=[_file("src/main.py", is_entry_point=True)],
        external_systems=({"name": "fastapi", "ecosystem": "pypi"},),
    )
    ctx = spec.build_context(sig)
    assert ctx is not None
    assert ctx.archetype == "service"


def test_how_it_works_fires_on_cli_archetype_via_entry_point() -> None:
    spec = onboarding.get_spec(SLOT_HOW_IT_WORKS)
    assert spec is not None
    sig = _signals(
        files=[_file("src/cli/__main__.py", is_entry_point=True)],
        entry_points=["src/cli/__main__.py"],
    )
    ctx = spec.build_context(sig)
    assert ctx is not None
    assert ctx.archetype == "cli"


# ---------------------------------------------------------------------------
# Development guide — gated on ≥2 structural signals
# ---------------------------------------------------------------------------


def test_development_guide_skipped_for_unconventional_repo() -> None:
    spec = onboarding.get_spec(SLOT_DEVELOPMENT_GUIDE)
    assert spec is not None
    sig = _signals(files=[_file("src/a.py"), _file("src/b.py")])
    assert spec.build_context(sig) is None


def test_development_guide_fires_with_suffix_pattern_and_test_mirror() -> None:
    spec = onboarding.get_spec(SLOT_DEVELOPMENT_GUIDE)
    assert spec is not None
    files = [
        _file("src/auth_handler.py", symbols=["AuthHandler"]),
        _file("src/user_handler.py", symbols=["UserHandler"]),
        _file("src/billing_handler.py", symbols=["BillingHandler"]),
        _file("src/order_handler.py", symbols=["OrderHandler"]),
        _file("tests/test_auth_handler.py"),
        _file("tests/test_user_handler.py"),
        _file("tests/test_billing_handler.py"),
    ]
    sig = _signals(files=files)
    ctx = spec.build_context(sig)
    assert ctx is not None
    suffixes = {p.suffix for p in ctx.suffix_patterns}
    assert "handler" in suffixes
    assert ctx.test_mirror is not None
    assert ctx.test_mirror.test_root == "tests"


# ---------------------------------------------------------------------------
# Promoted-page tagging
# ---------------------------------------------------------------------------


def _make_page(page_type: str, target: str) -> GeneratedPage:
    return GeneratedPage(
        page_id=f"{page_type}:{target}",
        page_type=page_type,
        title=f"{page_type}: {target}",
        content="...",
        source_hash="hash",
        model_name="mock",
        provider_name="mock",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=GENERATION_LEVELS.get(page_type, 0),
        target_path=target,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_tag_promoted_pages_sets_onboarding_slot() -> None:
    pages = [
        _make_page("repo_overview", "testrepo"),
        _make_page("architecture_diagram", "testrepo"),
        _make_page("file_page", "src/a.py"),
    ]
    PageGenerator._tag_promoted_pages(pages)
    assert pages[0].metadata["onboarding_slot"] == "project_overview"
    assert pages[1].metadata["onboarding_slot"] == "architecture_guide"
    # Non-promoted pages stay untouched.
    assert "onboarding_slot" not in pages[2].metadata


# ---------------------------------------------------------------------------
# Guided tour — gated on having a real (multi-stop) tour
# ---------------------------------------------------------------------------


def _tour_stops(n: int) -> tuple[dict, ...]:
    return tuple(
        {
            "order": i + 1,
            "target_path": f"src/file_{i}.py",
            "page_type": "file_page",
            "title": f"file_{i}.py",
            "depth": i,
            "kind": "code",
            "reason": "reason",
        }
        for i in range(n)
    )


def test_guided_tour_skipped_without_enough_stops() -> None:
    spec = onboarding.get_spec("guided_tour")
    assert spec is not None
    sig = _signals(files=[_file("src/main.py", is_entry_point=True)], tour_stops=_tour_stops(1))
    assert spec.build_context(sig) is None


def test_guided_tour_builds_with_stops_and_summaries() -> None:
    spec = onboarding.get_spec("guided_tour")
    assert spec is not None
    sig = _signals(
        files=[_file("src/main.py", is_entry_point=True)],
        tour_stops=_tour_stops(3),
        layer_order=("API", "Service", "Data"),
        completed_page_summaries={"src/file_0.py": "Entry orchestrator."},
    )
    ctx = spec.build_context(sig)
    assert ctx is not None
    assert len(ctx.stops) == 3
    assert ctx.layer_order == ["API", "Service", "Data"]
    # Summary is attached to the matching stop.
    assert ctx.stops[0].summary == "Entry orchestrator."


# ---------------------------------------------------------------------------
# Templates render
# ---------------------------------------------------------------------------


def _jinja_env() -> jinja2.Environment:
    templates_dir = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "core"
        / "src"
        / "repowise"
        / "core"
        / "generation"
        / "templates"
    )
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates_dir)),
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )


@pytest.mark.parametrize(
    "subkind_slot,ctx_factory",
    [
        (
            SLOT_CODEBASE_MAP,
            lambda: onboarding.get_spec(SLOT_CODEBASE_MAP).build_context(
                _signals(files=[_file(f"src/m{i}.py", symbols=[f"M{i}"]) for i in range(6)])
            ),
        ),
        (
            SLOT_ACTIVE_LANDSCAPE,
            lambda: onboarding.get_spec(SLOT_ACTIVE_LANDSCAPE).build_context(
                _signals(
                    files=[_file(f"src/f{i}.py") for i in range(20)],
                    git_meta={
                        f"src/f{i}.py": {
                            "commit_count_90d": 10,
                            "is_hotspot": i < 3,
                            "primary_owner_name": "alice",
                            "age_days": 30,
                        }
                        for i in range(15)
                    },
                )
            ),
        ),
        (
            SLOT_GETTING_STARTED,
            lambda: onboarding.get_spec(SLOT_GETTING_STARTED).build_context(
                _signals(
                    files=[_file("src/a.py")],
                    external_systems=(
                        {"name": "fastapi", "ecosystem": "pypi", "category": "framework"},
                    ),
                )
            ),
        ),
        (
            SLOT_KEY_CONCEPTS,
            lambda: onboarding.get_spec(SLOT_KEY_CONCEPTS).build_context(
                _signals(
                    files=[
                        _file("src/handler.py", symbols=["Handler"]),
                        _file("src/router.py", symbols=["Router"]),
                        _file("src/middleware.py", symbols=["Middleware"]),
                        _file("src/session.py", symbols=["Session"]),
                    ],
                    pagerank={
                        "src/handler.py": 0.9,
                        "src/router.py": 0.8,
                        "src/middleware.py": 0.7,
                        "src/session.py": 0.6,
                    },
                )
            ),
        ),
        (
            SLOT_HOW_IT_WORKS,
            lambda: onboarding.get_spec(SLOT_HOW_IT_WORKS).build_context(
                _signals(
                    files=[_file("src/main.py", is_entry_point=True)],
                    external_systems=({"name": "fastapi", "ecosystem": "pypi"},),
                )
            ),
        ),
        (
            "guided_tour",
            lambda: onboarding.get_spec("guided_tour").build_context(
                _signals(
                    files=[_file("src/main.py", is_entry_point=True)],
                    tour_stops=_tour_stops(3),
                    layer_order=("API", "Service"),
                    completed_page_summaries={"src/file_0.py": "Entry orchestrator."},
                )
            ),
        ),
        (
            SLOT_DEVELOPMENT_GUIDE,
            lambda: onboarding.get_spec(SLOT_DEVELOPMENT_GUIDE).build_context(
                _signals(
                    files=[
                        _file("src/auth_handler.py"),
                        _file("src/user_handler.py"),
                        _file("src/billing_handler.py"),
                        _file("src/order_handler.py"),
                        _file("tests/test_auth_handler.py"),
                        _file("tests/test_user_handler.py"),
                        _file("tests/test_billing_handler.py"),
                    ]
                )
            ),
        ),
    ],
)
def test_subkind_template_renders(subkind_slot: str, ctx_factory: Any) -> None:
    spec = onboarding.get_spec(subkind_slot)
    assert spec is not None
    ctx = ctx_factory()
    assert ctx is not None, f"gate failed for {subkind_slot}"
    env = _jinja_env()
    template = env.get_template(f"onboarding/{spec.template}")
    rendered = template.render(ctx=ctx, slot=subkind_slot)
    assert rendered.strip()  # non-empty
    assert "{{" not in rendered  # no unrendered Jinja


def test_how_it_works_renders_curated_tour_steps() -> None:
    """Curated tour steps (target_path + reason, no nodeIds/description) must
    normalize into the strict template's expected shape — regression for the
    first docs run against a curated KG ('dict object' has no attribute
    'nodeIds')."""
    import dataclasses

    spec = onboarding.get_spec(SLOT_HOW_IT_WORKS)
    assert spec is not None
    sig = _signals(
        files=[_file("src/main.py", is_entry_point=True)],
        entry_points=["src/main.py"],
    )
    curated_steps = (
        {"order": 1, "target_path": "README.md", "page_type": "repo_overview",
         "title": "README.md", "depth": 0, "kind": "overview",
         "reason": "Start here for the end-to-end picture."},
        {"order": 2, "target_path": "src/main.py", "page_type": "file_page",
         "title": "main.py", "depth": 1, "kind": "code",
         "reason": "An entry point — execution and imports fan out from here."},
    )
    sig = dataclasses.replace(sig, kg_tour_steps=curated_steps)
    ctx = spec.build_context(sig)
    assert ctx is not None
    # Normalized: description fed from reason, nodeIds synthesized from target_path.
    assert ctx.kg_tour_steps[0]["description"].startswith("Start here")
    assert ctx.kg_tour_steps[1]["nodeIds"] == ["file:src/main.py"]

    rendered = _jinja_env().get_template("onboarding/how_it_works.j2").render(ctx=ctx)
    assert "`src/main.py`" in rendered
    assert "An entry point" in rendered


def test_how_it_works_renders_legacy_tour_steps() -> None:
    """The pre-curation step shape (nodeIds + description) keeps working."""
    import dataclasses

    spec = onboarding.get_spec(SLOT_HOW_IT_WORKS)
    assert spec is not None
    sig = _signals(
        files=[_file("src/main.py", is_entry_point=True)],
        entry_points=["src/main.py"],
    )
    legacy_steps = (
        {"order": 1, "title": "Start Here",
         "description": "Begin with the entry point.",
         "nodeIds": ["file:src/main.py"]},
    )
    sig = dataclasses.replace(sig, kg_tour_steps=legacy_steps)
    ctx = spec.build_context(sig)
    assert ctx is not None
    assert ctx.kg_tour_steps[0]["description"] == "Begin with the entry point."
    assert ctx.kg_tour_steps[0]["nodeIds"] == ["file:src/main.py"]
    rendered = _jinja_env().get_template("onboarding/how_it_works.j2").render(ctx=ctx)
    assert "`src/main.py`" in rendered
