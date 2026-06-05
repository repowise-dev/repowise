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
async def test_skeleton_missing_source_file(setup_mcp, tmp_path, monkeypatch):
    from repowise.server.mcp_server import _state, get_context

    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))  # nothing on disk
    result = await get_context(["src/auth/service.py"], include=["skeleton"])
    sk = result["targets"]["src/auth/service.py"]["skeleton"]
    assert "could not be read" in sk["error"]


@pytest.mark.asyncio
async def test_skeleton_not_included_by_default(setup_mcp, tmp_path, monkeypatch):
    from repowise.server.mcp_server import _state, get_context

    _write_source(tmp_path)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["src/auth/service.py"])
    assert "skeleton" not in result["targets"]["src/auth/service.py"]
