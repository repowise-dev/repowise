"""Byte-for-byte goldens of real MCP tool payloads.

Pins values, nesting and dict key order (part of the wire format), so a
refactor of the server code must serve identical bytes. Rewrite with
``REPOWISE_REWRITE_GOLDEN=1`` only for an intended behaviour change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import time_machine

from tests.unit.server.conftest import (
    assert_payload_golden,
    normalize_payload,
    path_placeholders,
)

GOLDEN_DIR = Path(__file__).parent.parent / "golden" / "mcp"
# A week after the fixture's index time, so index age is fixed too.
_CLOCK = datetime(2026, 3, 26, 12, 0, 0, tzinfo=UTC)

# The span the fixture's AuthService (10-100) and login (20-40) rows describe.
_SERVICE_SOURCE = "\n".join(
    ['"""Auth service."""', "", "from src.db.models import User", ""]
    + [""] * 5
    + [
        "class AuthService:",
        '    """Main authentication service."""',
        "",
        "    def __init__(self, store):",
        "        self.store = store",
        "",
        "    def _hash(self, password: str) -> str:",
        "        return password[::-1]",
        "",
        "",
        "    async def login(self, username: str, password: str) -> Token:",
        '        """Authenticate a user."""',
        "        user = await self.store.get(User, username)",
        "        if user is None:",
        "            return None",
        "        return self._hash(password)",
    ]
    + [f"    # line {n}" for n in range(26, 101)]
)


@pytest.fixture
async def repo_root(setup_mcp, tmp_path):
    """The fixture repo with live source and two embedded pages, at a fixed clock."""
    import repowise.server.mcp_server as mcp_mod

    source = tmp_path / "src" / "auth" / "service.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(_SERVICE_SOURCE + "\n", encoding="utf-8", newline="\n")
    await mcp_mod._vector_store.embed_and_upsert(
        "file_page:src/auth/service.py",
        "Auth Service - Main authentication service class",
        {"title": "Auth Service", "page_type": "file_page", "target_path": "src/auth/service.py"},
    )
    await mcp_mod._vector_store.embed_and_upsert(
        "file_page:src/db/models.py",
        "DB Models - SQLAlchemy ORM models",
        {"title": "DB Models", "page_type": "file_page", "target_path": "src/db/models.py"},
    )
    with time_machine.travel(_CLOCK, tick=False):
        yield tmp_path


def _check(name: str, result: dict, root: Path) -> None:
    assert_payload_golden(
        GOLDEN_DIR / f"{name}.json", normalize_payload(result, path_placeholders(root))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "target", "include"),
    [
        ("file", "src/auth/service.py", None),
        (
            "file_include",
            "src/auth/service.py",
            ["ownership", "last_change", "decisions", "callers"],
        ),
        ("module", "src/auth", None),
        ("symbol", "AuthService", None),
        # A graph node with no page, no git row and no symbols.
        ("graph_node_only", "tests/test_service.py", None),
        ("not_found", "nonexistent_thing_xyz", None),
    ],
)
async def test_get_context_payload_matches_the_golden(repo_root, name, target, include) -> None:
    from repowise.server.mcp_server import get_context

    result = await get_context([target], include=include)
    _check(f"get_context_{name}", result, repo_root)


@pytest.mark.asyncio
async def test_get_symbol_payload_matches_the_golden(repo_root) -> None:
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("src/auth/service.py::login")
    _check("get_symbol", result, repo_root)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "query"),
    [
        ("symbol", "AuthService"),
        ("hybrid", "how does AuthService handle authentication"),
        ("path", "src/auth/service.py"),
    ],
)
async def test_search_codebase_payload_matches_the_golden(repo_root, mode, query) -> None:
    from repowise.server.mcp_server import search_codebase

    result = await search_codebase(query, mode=mode)
    _check(f"search_codebase_{mode}", result, repo_root)


def test_change_risk_cross_repo_block_matches_the_golden(tmp_path) -> None:
    from tests.unit.server.test_mcp_change_risk_cross_repo import (
        PROVIDER_FILE,
        _block,
        _enricher,
        _link,
        _report,
    )

    links = [_link(), _link("mobile", "app/order.ts")]
    block = _block(_enricher(tmp_path, links, _report()), [PROVIDER_FILE, "README.md"])
    _check("change_risk_cross_repo", block, tmp_path)
