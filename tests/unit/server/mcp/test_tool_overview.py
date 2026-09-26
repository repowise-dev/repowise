"""Branch-level tests for ``tool_overview`` helpers the tool-level suites skip.

``test_overview*.py`` drive ``get_overview`` end to end over the shared seed,
which never carries a legacy title, a corrupt metadata blob, more than ten
communities, a workspace registry, or a failing health read. Each of those is a
path an agent's first call can take on a real index, so each is pinned here
against the helper that owns it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server import _state
from repowise.server.mcp_server import tool_overview as ov
from repowise.server.mcp_server._budget import OmissionCollector

# ---------------------------------------------------------------------------
# Titles and ordering
# ---------------------------------------------------------------------------


def test_title_falls_back_to_the_repo_name_without_an_overview_page():
    assert ov._resolve_title(None, SimpleNamespace(name="acme")) == "acme"


def test_legacy_placeholder_title_gets_the_real_repo_name():
    page = SimpleNamespace(title="Repository Overview: repo")
    assert ov._resolve_title(page, SimpleNamespace(name="acme")) == "Repository Overview: acme"


def test_title_substitution_is_exact_match_not_prefix():
    """A repo whose name starts with "repo" must not be rewritten into "repowisewise"."""
    page = SimpleNamespace(title="Repository Overview: repowise")
    assert (
        ov._resolve_title(page, SimpleNamespace(name="repowise"))
        == "Repository Overview: repowise"
    )


def test_non_numeric_section_sorts_as_placed_but_ahead_of_every_numbered_one():
    """A malformed section string does not raise; it sorts with the placed pages."""
    keys = sorted(["2", "x.y", None, "1.10", "1.9"], key=ov._section_sort_key)
    assert keys == ["x.y", "1.9", "1.10", "2", None]


def test_entry_points_beyond_fifteen_are_capped_and_recoverable(tmp_path):
    collector = OmissionCollector("get_overview", repo_root=tmp_path)
    ids = [f"src/entry_{i:02d}.py" for i in range(18)]
    assert ov._capped_entry_points(ids, collector) == ids[:15]
    ((label, body),) = collector._chunks
    assert "3 dropped" in label
    assert body.splitlines() == ids[15:]


def test_entry_points_at_the_cap_record_no_omission(tmp_path):
    collector = OmissionCollector("get_overview", repo_root=tmp_path)
    ids = [f"e{i}" for i in range(15)]
    assert ov._capped_entry_points(ids, collector) == ids
    assert collector._chunks == []


# ---------------------------------------------------------------------------
# Tool surface guide
# ---------------------------------------------------------------------------


def _row(name, *, tier="core", recipes=(), default=True, eligible=True):
    return {
        "name": name,
        "tier": tier,
        "description": f"{name} tool",
        "recipes": list(recipes),
        "default_single_repo": default,
        "eligible_single_repo": eligible,
        "default_workspace": default,
        "eligible_workspace": eligible,
    }


def test_tool_surface_drops_recipes_that_need_a_disabled_tool_and_lists_opt_ins():
    """A recipe is only advertised when every tool it names is actually served."""
    rows = [
        _row(
            "get_risk",
            recipes=[
                {"name": "solo", "call": "get_risk()", "requires": ["get_risk"]},
                {"name": "combo", "call": "x", "requires": ["get_risk", "get_dead_code"]},
            ],
        ),
        _row("get_dead_code", tier="specialist", default=False),
        _row("get_why", recipes=[{"name": "solo", "call": "dup", "requires": ["get_why"]}]),
    ]
    guide = ov._tool_surface_guide(
        is_workspace=False, rows=rows, enabled_names={"get_risk", "get_why"}
    )
    assert guide["mode"] == "single_repo"
    assert guide["enabled"] == ["get_risk", "get_why"]
    # "combo" needs get_dead_code; the second "solo" is a duplicate name.
    assert guide["recipes"] == [{"name": "solo", "call": "get_risk()"}]
    assert guide["opt_in"] == [
        {"name": "get_dead_code", "description": "get_dead_code tool", "enabled": False}
    ]
    assert guide["counts"] == {
        "enabled": 2,
        "default": 2,
        "eligible": 3,
        "opt_in_available": 1,
        "tiers": {"core": 2},
    }


def test_tool_surface_in_workspace_mode_reads_the_workspace_columns():
    rows = [_row("get_overview")]
    rows[0]["default_workspace"] = False
    guide = ov._tool_surface_guide(is_workspace=True, rows=rows, enabled_names={"get_overview"})
    assert guide["mode"] == "workspace"
    assert guide["tools"][0]["default"] is False
    assert guide["counts"]["default"] == 0


# ---------------------------------------------------------------------------
# Knowledge map and communities
# ---------------------------------------------------------------------------


def _git(path, email, name=None, pct=0.5):
    return SimpleNamespace(
        file_path=path,
        primary_owner_email=email,
        primary_owner_name=name,
        primary_owner_commit_pct=pct,
    )


def test_knowledge_map_is_empty_without_git_rows():
    assert ov._build_knowledge_map([]) == {}


def test_knowledge_map_names_top_three_owners_and_never_surfaces_an_email():
    rows = [
        _git("a.py", "alice@x.io", "Alice"),
        _git("b.py", "alice@x.io", "Alice"),
        _git("c.py", "alice@x.io", "Alice"),
        _git("d.py", "bot@ci.io", "bot@ci.io"),
        _git("e.py", "bot@ci.io", "bot@ci.io"),
        _git("f.py", "carol@x.io", None),
        _git("g.py", "dave@x.io", "Dave"),
        _git("h.py", "", "Nobody"),
    ]
    km = ov._build_knowledge_map(rows)
    assert [o["name"] for o in km["top_owners"]] == ["Alice", "bot", "carol"]
    assert km["top_owners"][0] == {"name": "Alice", "files_owned": 3, "percentage": 37.5}
    assert "@" not in json.dumps(km)


def _node(node_id, community_id, *, node_type="file", meta="{}"):
    return SimpleNamespace(
        node_id=node_id,
        node_type=node_type,
        community_id=community_id,
        community_meta_json=meta,
    )


def test_community_label_keeps_a_specific_heuristic_label():
    assert ov._community_display_label("Billing", [], 3, {"src"}) == "Billing"


def test_generic_community_label_falls_back_to_dominant_directory_then_cluster_id():
    generic = {"packages", "src", "lib", "core", "app", ""}
    members = [_node("src/payments/a.py", 1), _node("src/payments/b.py", 1), _node("x.py", 1)]
    assert ov._community_display_label("core", members, 1, generic) == "payments"
    assert ov._community_display_label("", [_node("top.py", 7)], 7, generic) == "cluster_7"


def test_community_summary_caps_at_ten_by_size_and_survives_bad_meta():
    nodes = []
    for cid in range(12):
        # Community `cid` has cid + 1 members, so size order is 11, 10, ... 0.
        for m in range(cid + 1):
            nodes.append(_node(f"pkg{cid}/deep{cid}/f{m}.py", cid, meta="{not json"))
    nodes.append(_node("pkg0/sym::Thing", 0, node_type="symbol"))

    summary = ov._build_community_summary(nodes)
    assert [c["id"] for c in summary] == list(range(11, 1, -1))
    assert summary[0] == {"id": 11, "label": "deep11", "size": 12}


# ---------------------------------------------------------------------------
# Guided tour metadata
# ---------------------------------------------------------------------------


def test_guided_tour_with_unparseable_metadata_adds_nothing():
    page = SimpleNamespace(metadata_json="{not json")
    result: dict = {}
    ov._build_guided_tour(page, result, {}, True)
    assert result == {}


def test_layer_order_is_attached_under_architecture_even_without_the_tour():
    page = SimpleNamespace(
        metadata_json=json.dumps(
            {"layer_order": ["api", "core"], "guided_tour": [{"kind": "k", "reason": "r"}]}
        )
    )
    result: dict = {"architecture": {"layers": []}}
    ov._build_guided_tour(page, result, {}, False)
    assert result == {"architecture": {"layers": [], "layer_order": ["api", "core"]}}


# ---------------------------------------------------------------------------
# Workspace footer
# ---------------------------------------------------------------------------


def _registry(aliases, default="api", root="/ws"):
    return SimpleNamespace(
        get_default_alias=lambda: default,
        get_all_aliases=lambda: list(aliases),
        workspace_root=root,
    )


def test_workspace_footer_is_absent_outside_a_workspace_and_for_a_lone_repo(monkeypatch):
    monkeypatch.setattr(_state, "_registry", None)
    assert ov._build_workspace_footer() is None
    monkeypatch.setattr(_state, "_registry", _registry(["api"]))
    assert ov._build_workspace_footer() is None


def test_workspace_footer_names_siblings_and_carries_cross_repo_summaries(monkeypatch):
    enricher = SimpleNamespace(
        has_data=True,
        has_contract_data=True,
        get_cross_repo_summary=lambda: {"co_changes": 4},
        get_contract_summary=lambda: {"links": 2},
    )
    monkeypatch.setattr(_state, "_registry", _registry(["api", "web", "worker"]))
    monkeypatch.setattr(_state, "_cross_repo_enricher", enricher)

    footer = ov._build_workspace_footer()
    assert footer["default_repo"] == "api"
    assert footer["other_repos"] == ["web", "worker"]
    assert footer["workspace_root"] == "/ws"
    assert "web, worker" in footer["hint"]
    assert footer["cross_repo"] == {"co_changes": 4}
    assert footer["contract_links"] == {"links": 2}


def test_workspace_footer_omits_contract_links_without_contract_data(monkeypatch):
    enricher = SimpleNamespace(
        has_data=True,
        has_contract_data=False,
        get_cross_repo_summary=lambda: {"co_changes": 1},
    )
    monkeypatch.setattr(_state, "_registry", _registry(["api", "web"]))
    monkeypatch.setattr(_state, "_cross_repo_enricher", enricher)

    footer = ov._build_workspace_footer()
    assert footer["cross_repo"] == {"co_changes": 1}
    assert "contract_links" not in footer


# ---------------------------------------------------------------------------
# DB-backed blocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_health_read_failure_degrades_to_an_empty_block(
    session, populated_db, monkeypatch
):
    """A broken health read must cost the block, never the whole overview call."""

    async def boom(*_a, **_k):
        raise RuntimeError("health tables unreadable")

    monkeypatch.setattr(ov, "_get_health_metrics", boom)
    repo = SimpleNamespace(id=populated_db)
    assert await ov._build_code_health(session, repo) == {}


@pytest.mark.asyncio
async def test_key_decisions_ignore_active_records_nobody_accepted(session, populated_db):
    """The status column alone does not make a decision "settled" on the front page."""
    from repowise.core.persistence.models import DecisionRecord

    record = await session.get(DecisionRecord, "dec1")
    record.status = "active"
    await session.flush()

    assert await ov._build_key_decisions(session, SimpleNamespace(id=populated_db)) == {}


@pytest.mark.asyncio
async def test_key_decisions_survive_corrupt_affected_files(session, populated_db):
    from repowise.core.persistence.crud.authority import accept_decision
    from repowise.core.persistence.models import DecisionRecord

    record = await session.get(DecisionRecord, "dec1")
    await accept_decision(session, record, accepter="test", evidence=["seed:dec1"])
    record.status = "active"
    record.affected_files_json = "{not json"
    await session.flush()

    block = await ov._build_key_decisions(session, SimpleNamespace(id=populated_db))
    assert [d["id"] for d in block["top_active"]] == ["dec1"]
    assert block["top_active"][0]["affected_files"] == []
    assert block["recent_reversals"] == []
