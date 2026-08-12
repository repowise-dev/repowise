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


def _write_drifted_source(tmp_path, rel="src/auth/service.py"):
    """Same file as ``_write_source`` but shifted down 5 lines.

    The indexed bounds (AuthService 10-100, login 20-40 from conftest) now point
    into the header filler: synthetic drift. AuthService really sits at line 15,
    login at 25. A skeleton that trusts the stored bounds would render a filler
    line as the AuthService "signature" and elide the real ``class`` line.
    """
    lines = ["import os", "import sys", "import json", "import re", "import abc"]  # 1-5
    lines += ["# preamble"] * 9  # 6-14
    lines.append("class AuthService:")  # line 15
    for n in range(16, 25):
        lines.append(f"    setup_{n} = {n}")
    lines.append("    async def login(self, username: str, password: str) -> Token:")  # 25
    for n in range(26, 46):
        lines.append(f"        step_{n} = {n}")
    for n in range(46, 106):
        lines.append(f"    tail_{n} = {n}")
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_skeleton_verifies_drifted_bounds(setup_mcp, tmp_path, monkeypatch):
    """``verified: True`` must be honest: drifted symbols are relocated, not garbled.

    Without the bounds gate the skeleton slices signatures at the stored (drifted)
    lines and would keep a filler line as the AuthService signature while eliding
    the real ``class`` def, all under a ``verified: True`` claim.
    """
    from repowise.server.mcp_server import _state, get_context

    _write_drifted_source(tmp_path)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))

    result = await get_context(["src/auth/service.py"], include=["skeleton"])
    sk = result["targets"]["src/auth/service.py"]["skeleton"]
    assert "error" not in sk
    assert sk["verified"] is True
    # The real signatures render at their relocated positions. Without the gate
    # these class/def lines fall inside the drifted "body" region and get elided,
    # so their presence proves relocation ran.
    assert "class AuthService:" in sk["text"]
    assert "async def login" in sk["text"]


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
async def test_no_skeleton_without_an_explicit_include(setup_mcp, tmp_path, monkeypatch):
    """A file target serves the symbol card, never source bytes, unless asked.

    service.py spans 100 lines and used to auto-upgrade to a skeleton above an
    80-line threshold, on the claim that the skeleton beat the bare signature
    list per token. Re-measured on pinned Textualize/rich the claim is
    inverted — rich/ansi.py's card is 2,171 characters against 6,585 with the
    auto skeleton, of which the skeleton text alone is 5,295 — so source is
    opt-in. See the note on the retired constant in ``tool_context/targets``.
    """
    from repowise.server.mcp_server import _state, get_context

    _write_source(tmp_path)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["src/auth/service.py"])
    card = result["targets"]["src/auth/service.py"]
    assert "skeleton" not in card
    # The navigation the skeleton used to displace survives: names,
    # signatures, line numbers, and the cheap card around them.
    assert card["docs"]["symbols"]
    assert card["docs"].get("summary") is not None
    assert "freshness" in card


@pytest.mark.asyncio
async def test_include_skeleton_still_serves_the_full_text(setup_mcp, tmp_path, monkeypatch):
    """The other direction: the opt-in path is untouched and carries the text."""
    from repowise.server.mcp_server import _state, get_context

    _write_source(tmp_path)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["src/auth/service.py"], include=["skeleton"])
    card = result["targets"]["src/auth/service.py"]
    assert "class AuthService:" in card["skeleton"]["text"]
    assert card["skeleton"]["verified"] is True


@pytest.mark.asyncio
async def test_untruncated_response_omits_the_empty_truncation_keys(
    setup_mcp, tmp_path, monkeypatch
):
    """60 characters of "nothing happened", on every response that fits."""
    from repowise.server.mcp_server import _state, get_context

    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["src/db/models.py"])
    assert not result.get("truncated")
    for key in ("truncated", "dropped_targets", "dropped_symbols"):
        assert key not in result, f"{key} shipped on an untruncated response"


@pytest.mark.asyncio
async def test_small_file_keeps_symbol_card(setup_mcp, tmp_path, monkeypatch):
    from repowise.server.mcp_server import _state, get_context

    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["src/db/models.py"])
    card = result["targets"]["src/db/models.py"]
    assert "skeleton" not in card
    assert card["docs"]["symbols"]


@pytest.mark.asyncio
async def test_compact_false_still_serves_no_skeleton(setup_mcp, tmp_path, monkeypatch):
    from repowise.server.mcp_server import _state, get_context

    _write_source(tmp_path)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["src/auth/service.py"], compact=False)
    card = result["targets"]["src/auth/service.py"]
    assert "skeleton" not in card
    assert card["docs"]["symbols"]


@pytest.mark.asyncio
async def test_default_card_survives_a_missing_source_file(setup_mcp, tmp_path, monkeypatch):
    # Nothing on disk. The default card never touches the source, so it is
    # unaffected; explicit include=["skeleton"] still errors (test above).
    from repowise.server.mcp_server import _state, get_context

    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    result = await get_context(["src/auth/service.py"])
    card = result["targets"]["src/auth/service.py"]
    assert "skeleton" not in card
    assert card["docs"]["symbols"]
