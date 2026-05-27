"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_get_context_single_file(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(
        ["src/auth/service.py"],
        include=["docs", "full_doc", "ownership", "last_change", "decisions", "freshness"],
        compact=False,
    )
    targets = result["targets"]
    assert "src/auth/service.py" in targets
    t = targets["src/auth/service.py"]
    assert t["type"] == "file"
    # Docs
    assert t["docs"]["title"] == "Auth Service"
    assert "AuthService" in t["docs"]["content_md"]
    assert len(t["docs"]["symbols"]) == 2
    assert any(s["name"] == "AuthService" for s in t["docs"]["symbols"])
    assert "src/auth/middleware.py" in t["docs"]["imported_by"]
    # Ownership
    assert t["ownership"]["primary_owner"] == "Alice"
    assert t["ownership"]["owner_pct"] == 0.65
    assert t["ownership"]["contributor_count"] == 2
    # Last change
    assert t["last_change"]["author"] == "Alice"
    assert t["last_change"]["days_ago"] == 443
    # Decisions
    assert len(t["decisions"]) >= 1
    assert any(d["title"] == "Use JWT for authentication" for d in t["decisions"])
    # Freshness
    assert t["freshness"]["confidence_score"] == 0.85
    assert t["freshness"]["freshness_status"] == "fresh"
    assert t["freshness"]["is_stale"] is False


@pytest.mark.asyncio
async def test_get_context_single_module(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(
        ["src/auth"],
        include=["docs", "full_doc", "ownership", "last_change", "decisions", "freshness"],
        compact=False,
    )
    targets = result["targets"]
    assert "src/auth" in targets
    t = targets["src/auth"]
    assert t["type"] == "module"
    assert t["docs"]["title"] == "Auth Module"
    assert "authentication" in t["docs"]["content_md"].lower()
    assert len(t["docs"]["files"]) == 2  # service.py and middleware.py
    # Freshness from module page
    assert t["freshness"]["confidence_score"] == 0.95


@pytest.mark.asyncio
async def test_get_context_single_symbol(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(
        ["AuthService"],
        include=["docs", "full_doc"],
        compact=False,
    )
    targets = result["targets"]
    assert "AuthService" in targets
    t = targets["AuthService"]
    assert t["type"] == "symbol"
    assert t["docs"]["name"] == "AuthService"
    assert t["docs"]["kind"] == "class"
    assert t["docs"]["signature"] == "class AuthService"
    assert t["docs"]["file_path"] == "src/auth/service.py"
    assert t["docs"]["documentation"]  # Has content from file page


@pytest.mark.asyncio
async def test_get_context_multiple_targets(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth/service.py", "src/auth", "AuthService"])
    targets = result["targets"]
    assert len(targets) == 3
    assert targets["src/auth/service.py"]["type"] == "file"
    assert targets["src/auth"]["type"] == "module"
    assert targets["AuthService"]["type"] == "symbol"


@pytest.mark.asyncio
async def test_get_context_include_filter(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth/service.py"], include=["docs"])
    t = result["targets"]["src/auth/service.py"]
    assert "docs" in t
    assert "ownership" not in t
    assert "last_change" not in t
    assert "decisions" not in t
    assert "freshness" not in t


@pytest.mark.asyncio
async def test_get_context_not_found(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["nonexistent_thing_xyz"])
    t = result["targets"]["nonexistent_thing_xyz"]
    assert "error" in t


def _make_big_response(n_targets: int = 5, n_symbols: int = 80, body_chars: int = 4000) -> dict:
    """Build a synthetic get_context response well over the 32 KB budget."""
    targets = {}
    for i in range(n_targets):
        name = f"pkg/mod_{i}/file_{i}.ext"
        targets[name] = {
            "target": name,
            "type": "file",
            "docs": {
                "title": f"File {i}",
                "summary": "s" * 200,
                "content_md": "x" * body_chars,
                "symbols": [
                    {
                        "name": f"Sym{i}_{j}",
                        "kind": "class" if j % 5 == 0 else "function",
                        "signature": f"sig_{j}(...)",
                        "start_line": j * 10,
                        "end_line": j * 10 + 8,
                        "docstring": "d" * 300,
                    }
                    for j in range(n_symbols)
                ],
            },
        }
    return {"targets": targets, "_meta": {"timing_ms": 1.0}}


def test_truncate_to_budget_enforces_cap():
    from repowise.server.mcp_server.tool_context import (
        _CHAR_BUDGET,
        _truncate_to_budget,
    )

    big = _make_big_response()
    raw_size = len(json.dumps(big, separators=(",", ":"), default=str))
    assert raw_size > _CHAR_BUDGET, "fixture must exceed budget to be meaningful"

    out = _truncate_to_budget(big)
    final_size = len(json.dumps(out, separators=(",", ":"), default=str))
    assert final_size <= _CHAR_BUDGET
    assert out["truncated"] is True
    # At least one target must survive.
    assert len(out["targets"]) >= 1


def test_truncate_flags_and_dropped_fields_populate():
    from repowise.server.mcp_server.tool_context import _truncate_to_budget

    big = _make_big_response(n_targets=6, n_symbols=60, body_chars=5000)
    out = _truncate_to_budget(big)

    assert out["truncated"] is True
    # Either whole targets were dropped, or individual symbols were dropped —
    # both are acceptable outcomes; at least one must be populated.
    dropped_any = bool(out["dropped_targets"]) or bool(out["dropped_symbols"])
    assert dropped_any
    # Heavy optional fields should have been stripped from surviving targets.
    for tgt in out["targets"].values():
        assert "content_md" not in tgt.get("docs", {})
    # Dropped symbol lists (if any) must reference actual symbol names.
    for tgt_name, names in out["dropped_symbols"].items():
        assert tgt_name in big["targets"] or tgt_name not in out["targets"]
        assert all(isinstance(n, str) for n in names)


def test_truncate_noop_when_under_budget():
    from repowise.server.mcp_server.tool_context import _truncate_to_budget

    small = {
        "targets": {
            "a.py": {
                "target": "a.py",
                "type": "file",
                "docs": {"title": "A", "symbols": [{"name": "f", "kind": "function"}]},
            }
        },
        "_meta": {},
    }
    out = _truncate_to_budget(small)
    assert out["truncated"] is False
    assert out["dropped_targets"] == []
    assert out["dropped_symbols"] == {}
    assert "content_md" not in out["targets"]["a.py"]["docs"]  # wasn't there anyway
