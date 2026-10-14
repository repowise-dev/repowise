"""``component:<name>`` targets in get_context and include=["components"] in get_overview."""

from __future__ import annotations

import json

import pytest

_CATALOG = """\
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: auth
  title: Sign-in
  annotations:
    repowise.dev/criticality: critical
    repowise.dev/tags: pci, customer-facing
spec: {type: service, lifecycle: production, owner: group:default/identity}
---
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: billing
  annotations:
    repowise.dev/paths: src/billing/**
spec: {type: service, lifecycle: experimental, owner: group:default/money}
---
apiVersion: backstage.io/v1alpha1
kind: Gadget
metadata: {name: nope}
"""


def _write_catalog(root) -> None:
    (root / ".repowise").mkdir(exist_ok=True)
    (root / ".repowise" / "components.yaml").write_text(_CATALOG, encoding="utf-8")


def _without_meta(payload: dict) -> str:
    return json.dumps({k: v for k, v in payload.items() if k != "_meta"}, sort_keys=True)


@pytest.mark.asyncio
async def test_overview_lists_derived_components_only_when_asked(setup_mcp):
    from repowise.server.mcp_server import get_overview

    default = await get_overview()
    assert "components" not in default
    assert "components" not in default["more"]

    block = (await get_overview(include=["components"]))["components"]
    assert block["items"] == [
        {"name": "auth", "source": "derived", "derived_from": "module", "file_count": 2},
        {"name": "db", "source": "derived", "derived_from": "module", "file_count": 1},
    ]
    assert block["total"] == 2
    assert block["unassigned_files"] == 1  # tests/test_service.py sits under no module
    assert "catalog_errors" not in block
    assert "component:<name>" in block["hint"]


@pytest.mark.asyncio
async def test_overview_merges_declared_components_over_derived(setup_mcp, tmp_path):
    from repowise.server.mcp_server import get_overview

    _write_catalog(tmp_path)
    block = (await get_overview(include=["components"]))["components"]
    names = [row["name"] for row in block["items"]]
    assert names == ["auth", "billing", "db"]
    auth = block["items"][0]
    assert auth == {
        "name": "auth",
        "source": "declared",
        "owner": "group:default/identity",
        "component_type": "service",
        "lifecycle": "production",
        "criticality": "critical",
        "tags": ["pci", "customer-facing"],
        "file_count": 2,  # no paths of its own: inherits the derived module's
    }
    assert block["items"][1]["file_count"] == 0
    assert block["catalog_errors"] == 1  # the unknown kind


@pytest.mark.asyncio
async def test_component_target_rolls_up_existing_facts(setup_mcp, session, tmp_path):
    from repowise.core.persistence.models import HealthFileMetric
    from repowise.server.mcp_server import get_context

    session.add_all(
        [
            HealthFileMetric(
                repository_id="repo1",
                file_path="src/auth/service.py",
                score=6.0,
                nloc=100,
                has_test_file=True,
            ),
            HealthFileMetric(
                repository_id="repo1",
                file_path="src/auth/middleware.py",
                score=9.0,
                nloc=100,
                has_test_file=False,
            ),
        ]
    )
    await session.commit()
    _write_catalog(tmp_path)

    result = await get_context(targets=["component:auth", "src/db/models.py"])
    card = result["targets"]["component:auth"]
    assert card["type"] == "component"
    assert (card["name"], card["source"], card["title"]) == ("auth", "declared", "Sign-in")
    assert card["declared_in"] == ".repowise/components.yaml"
    assert card["paths"] == ["src/auth/**"]
    assert card["file_count"] == 2
    assert card["sample_files"] == ["src/auth/middleware.py", "src/auth/service.py"]
    facts = card["facts"]
    assert facts["health"]["average"] == 7.5
    assert facts["health"]["worst_path"] == "src/auth/service.py"
    assert facts["hotspots"]["count"] == 1
    assert facts["hotspots"]["top"] == ["src/auth/service.py"]
    assert facts["bus_factor"]["value"] == 1
    assert facts["test_reach"]["share"] == 0.5
    assert facts["dead_code"]["findings"] == 1 and facts["dead_code"]["lines"] == 20
    assert all(figure["basis"] for figure in facts.values())
    assert "Alice" not in json.dumps(card)
    # A file target in the same batch still resolves the ordinary way.
    assert "src/db/models.py" in result["targets"]


@pytest.mark.asyncio
async def test_unknown_component_names_the_available_ones(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=["component:nope"])
    card = result["targets"]["component:nope"]
    assert card["error"] == "No component named 'nope'."
    assert card["available"] == ["auth", "db"]


@pytest.mark.asyncio
async def test_default_responses_are_byte_identical_with_a_catalog(setup_mcp, tmp_path):
    from repowise.server.mcp_server import get_context, get_overview

    overview_before = _without_meta(await get_overview())
    context_before = _without_meta(await get_context(targets=["src/auth/service.py"]))
    _write_catalog(tmp_path)
    (tmp_path / "catalog-info.yaml").write_text(_CATALOG, encoding="utf-8")

    assert _without_meta(await get_overview()) == overview_before
    assert _without_meta(await get_context(targets=["src/auth/service.py"])) == context_before
