"""get_context include=["skeleton"] — the distill skeleton block."""

from __future__ import annotations

import pytest


def _write_source(tmp_path, rel="src/auth/service.py", total_lines=100):
    """A real on-disk file matching the populated WikiSymbol bounds.

    AuthService spans 10-100, login 20-40 (see conftest). Everything else is
    filler so the body elision has something to elide.
    """
    lines = ["import os", "import sys"] + ["# preamble"] * 7  # lines 1-9
    lines.append("class AuthService:")  # line 10
    for n in range(11, 20):
        lines.append(f"    setup_{n} = {n}")
    lines.append("    async def login(self, username: str, password: str) -> Token:")  # 20
    for n in range(21, 41):
        lines.append(f"        step_{n} = {n}")
    for n in range(41, total_lines + 1):
        lines.append(f"    tail_{n} = {n}")
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_skeleton_block_for_file_target(setup_mcp, tmp_path, monkeypatch):
    from repowise.server.mcp_server import _state, get_context

    _write_source(tmp_path)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))

    result = await get_context(["src/auth/service.py"], include=["skeleton"])
    sk = result["targets"]["src/auth/service.py"]["skeleton"]
    assert "error" not in sk
    assert sk["mode"] == "smart"
    assert "class AuthService:" in sk["text"]
    assert "async def login" in sk["text"]
    assert sk["tokens"] < sk["full_tokens"]
    assert "... " in sk["text"]  # at least one elision marker


@pytest.mark.asyncio
async def test_skeleton_requires_file_target(setup_mcp, tmp_path, monkeypatch):
    from repowise.server.mcp_server import _state, get_context

    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["AuthService"], include=["skeleton"])
    sk = result["targets"]["AuthService"]["skeleton"]
    assert "file target" in sk["error"]


@pytest.mark.asyncio
async def test_skeleton_for_symbol_target_renders_defining_file(
    setup_mcp, session, tmp_path, monkeypatch
):
    """A "file.py::Symbol" target must skeleton the DEFINING FILE, not fail
    with an opaque read error on the literal "::"-path (S2 dogfood bug)."""
    from repowise.core.persistence.models import GraphNode, Repository
    from repowise.server.mcp_server import _state, get_context

    _write_source(tmp_path)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))

    # get_context resolves a "::Symbol" target via its symbol graph node; the
    # base fixture only has file nodes, so add one for login.
    repo = (await session.execute(__import__("sqlalchemy").select(Repository))).scalars().first()
    target_id = "src/auth/service.py::login"
    session.add(
        GraphNode(
            id="sk_login",
            repository_id=repo.id,
            node_id=target_id,
            node_type="symbol",
            name="login",
            file_path="src/auth/service.py",
            kind="method",
            start_line=20,
            end_line=40,
        )
    )
    await session.flush()

    result = await get_context([target_id], include=["skeleton"])
    sk = result["targets"][target_id]["skeleton"]
    assert "error" not in sk
    assert "class AuthService:" in sk["text"]  # whole file, not just the symbol
    assert sk["of_file"] == "src/auth/service.py"
    assert "get_symbol" in sk["symbol_hint"]


@pytest.mark.asyncio
async def test_skeleton_missing_source_file(setup_mcp, tmp_path, monkeypatch):
    from repowise.server.mcp_server import _state, get_context

    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))  # nothing on disk
    result = await get_context(["src/auth/service.py"], include=["skeleton"])
    sk = result["targets"]["src/auth/service.py"]["skeleton"]
    assert "could not be read" in sk["error"]


@pytest.mark.asyncio
async def test_skeleton_is_default_for_large_file_targets(setup_mcp, tmp_path, monkeypatch):
    # service.py spans 100 lines (> the 80-line threshold): the default card
    # auto-upgrades to the skeleton and drops the redundant symbol list.
    from repowise.server.mcp_server import _state, get_context

    _write_source(tmp_path)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["src/auth/service.py"])
    card = result["targets"]["src/auth/service.py"]
    sk = card["skeleton"]
    assert sk["auto"] is True
    assert "compact=False" in sk["opt_out_hint"]
    assert "class AuthService:" in sk["text"]
    assert "symbols" not in card["docs"]
    # Summary and freshness still ride along.
    assert card["docs"].get("summary") is not None
    assert "freshness" in card


@pytest.mark.asyncio
async def test_small_file_keeps_symbol_card(setup_mcp, tmp_path, monkeypatch):
    # models.py's symbols end at line 30 — below the threshold, no skeleton.
    from repowise.server.mcp_server import _state, get_context

    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["src/db/models.py"])
    card = result["targets"]["src/db/models.py"]
    assert "skeleton" not in card
    assert card["docs"]["symbols"]


@pytest.mark.asyncio
async def test_compact_false_opts_out_of_auto_skeleton(setup_mcp, tmp_path, monkeypatch):
    from repowise.server.mcp_server import _state, get_context

    _write_source(tmp_path)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["src/auth/service.py"], compact=False)
    card = result["targets"]["src/auth/service.py"]
    assert "skeleton" not in card
    assert card["docs"]["symbols"]


@pytest.mark.asyncio
async def test_auto_skeleton_falls_back_to_card_when_source_missing(
    setup_mcp, tmp_path, monkeypatch
):
    # Nothing on disk: the auto-upgrade must degrade to the symbol card, not
    # to an error-only response (explicit include=["skeleton"] still errors).
    from repowise.server.mcp_server import _state, get_context

    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["src/auth/service.py"])
    card = result["targets"]["src/auth/service.py"]
    assert "skeleton" not in card
    assert card["docs"]["symbols"]
