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
    assert "hint" not in block


@pytest.mark.asyncio
async def test_component_file_counts_leave_out_import_targets(setup_mcp, session):
    """A path an edge names but the index never parsed is not a component file."""
    from repowise.core.persistence.models import GraphNode
    from repowise.server.mcp_server import get_overview

    session.add_all(
        [
            GraphNode(
                repository_id="repo1",
                node_id="src/auth/schema.gen.ts",
                node_type="file",
                language="unknown",
            ),
            GraphNode(
                repository_id="repo1",
                node_id="external:requests",
                node_type="file",
                language="external",
            ),
        ]
    )
    await session.commit()

    block = (await get_overview(include=["components"]))["components"]
    assert block["items"][0] == {
        "name": "auth",
        "source": "derived",
        "derived_from": "module",
        "file_count": 2,
    }
    assert block["unassigned_files"] == 1


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
    assert facts["bus_factor"]["owned_files"] == 1  # middleware.py has no git row
    assert facts["bus_factor"]["basis"] == "inferred"
    assert facts["test_reach"]["share"] == 0.5
    assert facts["dead_code"]["findings"] == 1 and facts["dead_code"]["lines"] == 20
    assert all(figure["basis"] for figure in facts.values())
    assert facts["dead_code"]["basis"] == "inferred"  # the fixture has findings, so it ran
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


@pytest.mark.asyncio
async def test_a_declared_component_that_claims_nothing_says_why(setup_mcp, tmp_path):
    from repowise.server.mcp_server import get_context

    (tmp_path / "catalog-info.yaml").write_text(
        "apiVersion: backstage.io/v1alpha1\nkind: Component\nmetadata: {name: ghost}\n"
        "spec: {type: service, lifecycle: production, owner: platform}\n",
        encoding="utf-8",
    )
    card = (await get_context(targets=["component:ghost"]))["targets"]["component:ghost"]
    assert card["file_count"] == 0
    assert "claims no files" in card["note"]
    assert {f["basis"] for f in card["facts"].values()} == {"unavailable"}
    assert "sample_files" not in card


@pytest.mark.asyncio
async def test_several_component_targets_resolve_components_once(setup_mcp, monkeypatch):
    from repowise.server.mcp_server import _components, get_context

    calls = []
    real = _components.load_repo_components

    async def counting(*args, **kwargs):
        calls.append(1)
        return await real(*args, **kwargs)

    monkeypatch.setattr(_components, "load_repo_components", counting)
    result = await get_context(targets=["component:auth", "component:db", "src/db/models.py"])
    assert set(result["targets"]) == {"component:auth", "component:db", "src/db/models.py"}
    assert len(calls) == 1
    await get_context(targets=["component:auth"])
    assert len(calls) == 1  # cached for the same index and catalog state


@pytest.mark.asyncio
async def test_a_component_batch_reads_each_fact_table_once(setup_mcp, tmp_path, monkeypatch):
    from repowise.core.components import store

    (tmp_path / "catalog-info.yaml").write_text(
        "apiVersion: backstage.io/v1alpha1\nkind: Component\n"
        "metadata:\n  name: middleware\n"
        "  annotations: {repowise.dev/paths: src/auth/middleware.py}\n"
        "spec: {type: service, lifecycle: production, owner: platform}\n",
        encoding="utf-8",
    )
    reads: list[int] = []
    runs: list[int] = []
    real_rows, real_ran = store._rows_for, store.dead_code_ran

    async def rows_for(*args, **kwargs):
        reads.append(1)
        return await real_rows(*args, **kwargs)

    async def ran(*args, **kwargs):
        runs.append(1)
        return await real_ran(*args, **kwargs)

    monkeypatch.setattr(store, "_rows_for", rows_for)
    monkeypatch.setattr(store, "dead_code_ran", ran)
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=["component:auth", "component:db", "component:middleware"])
    assert all("facts" in result["targets"][t] for t in result["targets"])
    # git, health and dead-code rows once each, and the run status once, for three components.
    assert (len(reads), len(runs)) == (3, 1)


_TEAMS = """\
apiVersion: backstage.io/v1alpha1
kind: Group
metadata:
  name: identity
  annotations:
    repowise.dev/member-emails: carol@example.com
spec: {type: team, children: []}
"""


@pytest.mark.asyncio
async def test_component_ownership_only_with_include(setup_mcp, session, tmp_path):
    from datetime import UTC, datetime

    from repowise.core.persistence.models import GitCommit, GitCommitFile
    from repowise.server.mcp_server import get_context
    from repowise.server.mcp_server._components import reset_cache

    when = datetime(2026, 9, 1, tzinfo=UTC)
    session.add_all(
        [
            GitCommit(
                repository_id="repo1",
                sha="a" * 40,
                author_name="Carol Diaz",
                author_email="carol@example.com",
                committed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            GitCommit(
                repository_id="repo1",
                sha="b" * 40,
                author_name="Dan Wu",
                author_email="dan@example.com",
                committed_at=when,
            ),
            GitCommitFile(repository_id="repo1", sha="a" * 40, file_path="src/auth/service.py"),
            GitCommitFile(repository_id="repo1", sha="b" * 40, file_path="src/auth/service.py"),
        ]
    )
    await session.commit()
    _write_catalog(tmp_path)
    (tmp_path / ".repowise" / "teams.yaml").write_text(_TEAMS, encoding="utf-8")
    reset_cache()

    plain = (await get_context(targets=["component:auth"]))["targets"]["component:auth"]
    assert "ownership" not in plain

    result = await get_context(
        targets=["component:auth", "component:billing"], include=["ownership"]
    )
    auth = result["targets"]["component:auth"]["ownership"]
    assert auth["declared_teams"] == [
        {"team": "identity", "files": 2, "activity": "no_recent_changes"}
    ]
    assert [f["kind"] for f in auth["findings"]] == [
        "declared_owner_inactive",
        "critical_no_active_owner",
    ]
    assert auth["recent"]["commits"] == 1
    billing = result["targets"]["component:billing"]["ownership"]
    assert [f["kind"] for f in billing["findings"]] == ["catalog_entry_no_code"]
    text = json.dumps(result["targets"])
    assert "carol" not in text.lower() and "dan@" not in text
