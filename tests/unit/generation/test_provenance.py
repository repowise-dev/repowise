"""Unit tests for file-page provenance + layer metadata attachment."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.page_generator.core import _attach_file_provenance


def _page() -> GeneratedPage:
    return GeneratedPage(
        page_id="file_page:pkg/foo.py",
        page_type="file_page",
        title="File: pkg/foo.py",
        content="",
        source_hash="x",
        model_name="mock",
        provider_name="mock",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=2,
        target_path="pkg/foo.py",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _ctx(**over):
    base = dict(
        file_path="pkg/foo.py",
        kg_layer_name="",
        kg_layer_id="",
        kg_layer_role="",
        dependencies=[],
        decision_records=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_layer_metadata_attached():
    page = _page()
    _attach_file_provenance(
        page,
        _ctx(
            kg_layer_name="Domain",
            kg_layer_id="layer:domain",
            kg_layer_role="entry_point",
        ),
    )
    assert page.metadata["layer_name"] == "Domain"
    # Stable slug id is attached so the UI joins file -> layer page by id.
    assert page.metadata["layer_id"] == "layer:domain"
    assert page.metadata["layer_role"] == "entry_point"


def test_no_kg_layer_falls_back_to_inferred_layer():
    # Every file page must carry a layer_name so the Architecture tree can
    # group it; with no KG layer it is inferred from the path, and the
    # layer_id is the slug of that inferred name.
    page = _page()
    _attach_file_provenance(page, _ctx(file_path="src/api/users.py"))
    assert page.metadata["layer_name"] == "API"
    assert page.metadata["layer_id"] == "layer:api"
    # No KG role is invented for the fallback path.
    assert "layer_role" not in page.metadata


def test_sources_from_dependencies_and_decisions():
    page = _page()
    ctx = _ctx(
        dependencies=["pkg/bar.py", "pkg/baz.py"],
        decision_records=[{"evidence_file": "docs/adr-1.md"}, {"source": "pkg/bar.py"}],
    )
    _attach_file_provenance(page, ctx)
    sources = page.metadata["sources"]
    paths = [s["path"] for s in sources]
    assert "pkg/bar.py" in paths
    assert "pkg/baz.py" in paths
    assert "docs/adr-1.md" in paths
    # bar.py already recorded as a dependency — not duplicated by the decision.
    assert paths.count("pkg/bar.py") == 1
    assert {s["kind"] for s in sources} == {"dependency", "decision"}


def test_no_sources_means_no_key():
    page = _page()
    _attach_file_provenance(page, _ctx())
    assert "sources" not in page.metadata


def test_dependencies_capped_at_ten():
    page = _page()
    _attach_file_provenance(page, _ctx(dependencies=[f"d{i}.py" for i in range(20)]))
    assert len(page.metadata["sources"]) == 10
