"""L1 System Context: which externals it shows and what it says the system is."""

from __future__ import annotations

import json

import pytest

from repowise.core.persistence import bulk_upsert_external_systems, upsert_repository
from repowise.server.services import c4_builder
from repowise.server.services.c4_builder.mermaid import to_mermaid_l1

_EMPTY_NOTE = "No external service dependencies detected"


def _ext(name: str, category: str, *, dev: bool = False, declared_in: str = "package.json"):
    return {
        "name": name,
        "display_name": name,
        "ecosystem": "npm",
        "category": category,
        "version": "1",
        "declared_in": declared_in,
        "is_dev_dep": dev,
    }


async def _repo(session, tmp_path, externals):
    repo = await upsert_repository(session, name="demo", local_path=str(tmp_path))
    if externals:
        await bulk_upsert_external_systems(session, repo.id, externals)
    await session.commit()
    return repo


@pytest.mark.asyncio
async def test_l1_shows_only_runtime_services_and_frameworks(async_session, tmp_path):
    repo = await _repo(
        async_session,
        tmp_path,
        [
            _ext("stripe", "service"),
            _ext("react", "framework"),
            _ext("zod", "library"),
            _ext("vitest", "tool", dev=True),
            _ext("@sentry/node", "service", dev=True),
            # Dev in one manifest, runtime in another: still a runtime dep.
            _ext("express", "framework", dev=True, declared_in="a/package.json"),
            _ext("express", "framework", declared_in="b/package.json"),
        ],
    )
    view = await c4_builder.build_l1(async_session, repo.id)
    assert {e.name for e in view.external_systems} == {"stripe", "react", "express"}
    system_edges = [r for r in view.relations if r.source_id == view.system.id]
    assert len(system_edges) == 3

    # Libraries still reach the lower levels' registry.
    all_views, _ = await c4_builder._external_views(async_session, repo.id)
    assert "zod" in {e.name for e in all_views}


@pytest.mark.asyncio
async def test_l1_ignores_deps_declared_by_examples_tests_and_docs(async_session, tmp_path):
    repo = await _repo(
        async_session,
        tmp_path,
        [
            _ext("redis", "service", declared_in="examples/celery/pyproject.toml"),
            _ext("next", "framework", declared_in="docs/package.json"),
            _ext("stripe", "service", declared_in="tests/fixtures/app/package.json"),
            _ext("openai", "service", declared_in="packages/core/package.json"),
        ],
    )
    view = await c4_builder.build_l1(async_session, repo.id)
    assert {e.name for e in view.external_systems} == {"openai"}


@pytest.mark.asyncio
async def test_empty_l1_says_so(async_session, tmp_path):
    repo = await _repo(async_session, tmp_path, [_ext("zod", "library")])
    view = await c4_builder.build_l1(async_session, repo.id)
    assert view.external_systems == []
    assert _EMPTY_NOTE in to_mermaid_l1(view)


@pytest.mark.asyncio
async def test_system_description_from_root_manifest(async_session, tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "description": "Streams model output."}), encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Demo\n\nSomething else.\n", encoding="utf-8")
    repo = await _repo(async_session, tmp_path, [])
    view = await c4_builder.build_l1(async_session, repo.id)
    assert view.system.description == "Streams model output."


@pytest.mark.asyncio
async def test_system_description_falls_back_to_readme(async_session, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "# Demo\n\n![badge](x.svg)\n\nA tiny web framework.\n\n## Install\n\npip install demo\n",
        encoding="utf-8",
    )
    repo = await _repo(async_session, tmp_path, [])
    view = await c4_builder.build_l1(async_session, repo.id)
    assert view.system.description == "A tiny web framework."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "readme",
    [
        "A tiny web framework.\n\nMore detail.\n",
        "![badge](x.svg)\n\nA tiny web framework.\n\n## Install\n\nUse pip.\n",
    ],
    ids=["no-headings", "intro-before-first-heading"],
)
async def test_system_description_reads_unheaded_readme_intro(async_session, tmp_path, readme):
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    repo = await _repo(async_session, tmp_path, [])
    view = await c4_builder.build_l1(async_session, repo.id)
    assert view.system.description == "A tiny web framework."


@pytest.mark.asyncio
async def test_system_description_empty_without_sources(async_session, tmp_path):
    repo = await _repo(async_session, tmp_path / "missing", [])
    view = await c4_builder.build_l1(async_session, repo.id)
    assert view.system.description == ""
