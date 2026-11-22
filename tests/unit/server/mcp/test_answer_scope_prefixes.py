"""A get_answer scope naming several directories, or one the index does not hold.

Agents pass "pkg/maps pkg/datapath" or "a/, b/" as one scope. Read as one
literal prefix it matched no file, and the reply said only "No wiki hits".
A scope naming a directory the index lacks got the same reply after a
whole-repository scan. Now several prefixes search their union, and a prefix
no indexed file lives under is named back with the nearest directories.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import GraphNode, Page

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)
_QUESTION = "how does the ledger reconcile work?"
_VAULT = "pkg/vault/ledger_reconcile.py"
_BILLING = "pkg/billing/ledger_reconcile.py"
_OTHER = "pkg/audit/ledger_reconcile.py"
_SPACED = "docs site/ledger_reconcile.py"


def _page(path: str) -> Page:
    content = "Ledger reconcile: reconcile the ledger totals."
    return Page(
        id=f"file_page:{path}",
        repository_id="repo1",
        page_type="file_page",
        title=f"File: {path}",
        content=content,
        summary=content,
        target_path=path,
        source_hash="h",
        model_name="mock",
        provider_name="mock",
        generation_level=2,
        confidence=1.0,
        freshness_status="fresh",
        metadata_json="{}",
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture
async def ledgers(setup_mcp, session, fts, factory, tmp_path, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)
    paths = [_VAULT, _BILLING, _OTHER, _SPACED]
    pages = [_page(p) for p in paths]
    session.add_all(pages)
    session.add_all(
        GraphNode(
            id=f"ledger{i}",
            repository_id=setup_mcp,
            node_id=p,
            node_type="file",
            language="python",
        )
        for i, p in enumerate(paths)
    )
    await session.commit()
    for p in pages:
        await fts.index(p.id, p.title, p.content, summary=p.summary, target_path=p.target_path)
    return SimpleNamespace(session_factory=factory, path=str(tmp_path), fts=fts)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["pkg/vault pkg/billing", "pkg/vault/, pkg/billing/"])
async def test_several_prefixes_search_their_union(ledgers, scope):
    from repowise.server.mcp_server import get_answer

    text = json.dumps(await get_answer(_QUESTION, scope=scope))

    assert _VAULT in text and _BILLING in text
    assert _OTHER not in text
    assert "No wiki hits" not in text


@pytest.mark.asyncio
async def test_a_scope_the_index_lacks_names_the_nearest_directories(ledgers, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    async def _never(*_a, **_k):
        raise AssertionError("a scope holding no indexed file searches nothing")

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _never)

    result = await get_answer(_QUESTION, scope="pkg/vaults")

    assert result["confidence"] == "low"
    assert "pkg/vaults/ holds no indexed file" in result["note"]
    assert "No wiki hits" not in result["note"]
    assert result["scope_suggestions"][0] == "pkg/vault/"


@pytest.mark.asyncio
async def test_a_missing_prefix_beside_an_indexed_one_is_named(ledgers):
    from repowise.server.mcp_server import get_answer

    result = await get_answer(_QUESTION, scope="pkg/vault pkg/agent")

    assert _VAULT in json.dumps(result)
    assert result["scope_note"] == "pkg/agent/ holds no indexed file; searched pkg/vault/ only."


@pytest.mark.asyncio
async def test_unscoped_and_fully_indexed_replies_carry_no_scope_keys(ledgers):
    from repowise.server.mcp_server import get_answer

    for scope in (None, "pkg/vault"):
        result = await get_answer(_QUESTION, scope=scope)
        assert "scope_note" not in result and "scope_suggestions" not in result


@pytest.mark.asyncio
async def test_resolution_keeps_an_indexed_path_with_a_space_and_folds_nested(ledgers):
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server.tool_answer.scope import resolve_scope

    async with get_session(ledgers.session_factory) as session:
        spaced = await resolve_scope(session, "repo1", "docs site")
        nested = await resolve_scope(session, "repo1", "pkg/vault pkg ./pkg/vault/")

    assert spaced.scope == "docs site" and not spaced.missing
    assert nested.scope == "pkg" and not nested.missing


@pytest.mark.asyncio
async def test_fts_path_filter_takes_several_prefixes(ledgers):
    rows = await ledgers.fts.search(_QUESTION, limit=10, path_prefix="pkg/vault\npkg/billing")

    assert sorted(r.target_path for r in rows) == [_BILLING, _VAULT]


def test_in_scope_reads_each_prefix_as_a_directory():
    from repowise.server.mcp_server._page_paths import in_scope

    assert in_scope("pkg/a/x.py", "pkg/b\npkg/a")
    assert not in_scope("pkg/ab/x.py", "pkg/b\npkg/a")
    assert in_scope("reports,2024/q1.py", "reports,2024")


def test_nearest_directories_start_inside_the_missing_ones_parent():
    from collections import Counter

    from repowise.server.mcp_server.tool_answer.scope import _nearest

    dirs = Counter(
        {
            "examples": 1,
            "examples/ip-masq-agent": 1,
            "pkg": 10,
            "pkg/monitor": 3,
            "pkg/monitor/agent": 3,
            "pkg/datapath": 7,
            "daemon": 4,
        }
    )

    # A name match outside the parent never displaces the parent's own directories.
    assert _nearest(["pkg/agent"], dirs) == ["pkg/monitor/agent/"]
    # At the root, every name match counts, shallowest first.
    assert _nearest(["masq-agent"], dirs) == ["examples/ip-masq-agent/", "pkg/monitor/agent/"]
    assert _nearest(["pkg/zzz"], dirs) == ["pkg/datapath/", "pkg/monitor/"]


@pytest.mark.asyncio
async def test_a_directory_of_documents_on_disk_is_searched_not_reported_missing(
    ledgers, tmp_path
):
    """Documents are served from disk, so their directory may hold no indexed row."""
    from repowise.server.mcp_server import get_answer

    (tmp_path / "handbook").mkdir()
    (tmp_path / "handbook" / "ledger.md").write_text("# Ledger reconcile\n", encoding="utf-8")
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "ledger.md").write_text("# Ledger\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "handbook/ledger.md"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)

    result = await get_answer(_QUESTION, scope="handbook")

    assert "holds no indexed file" not in json.dumps(result)
    assert "scope_suggestions" not in result
    # Untracked, so not a directory the answer reads from.
    untracked = await get_answer(_QUESTION, scope="scratch")
    assert "scratch/ holds no indexed file" in untracked["note"]


@pytest.mark.asyncio
async def test_an_indexed_directory_named_with_a_comma_stays_whole(ledgers, session):
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server.tool_answer.scope import resolve_scope

    session.add(
        GraphNode(
            id="comma",
            repository_id="repo1",
            node_id="reports,2024/q1.py",
            node_type="file",
            language="python",
        )
    )
    await session.commit()
    async with get_session(ledgers.session_factory) as s:
        whole = await resolve_scope(s, "repo1", "reports,2024/")
        separators = await resolve_scope(s, "repo1", " , ")

    assert whole.scope == "reports,2024" and not whole.missing
    assert separators == (None, [], [])
