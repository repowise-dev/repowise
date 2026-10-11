"""``get_symbol`` edge paths: corrupt or empty omission refs, excluded or
missing range reads, workspace references, vanished ambiguous candidates,
clamped ``context_lines`` and the budget-cut ambiguity note.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server import tool_symbol

# ---------------------------------------------------------------------------
# _correct_ambiguity_note
# ---------------------------------------------------------------------------


def _ambiguous(**extra):
    return {"ambiguous": True, "match_count": 4, "note": "All candidate bodies are included", **extra}


def test_ambiguity_note_is_rewritten_once_the_budget_cut_candidates():
    """The shape promises no silent pick; a cut that leaves that note standing breaks it."""
    result = _ambiguous(candidates_emitted=2)
    tool_symbol._correct_ambiguity_note(result, None)
    assert result["note"].startswith("4 symbols match this id")
    assert "2 candidate bodies fit this response" in result["note"]
    assert "All candidate bodies are included" not in result["note"]


@pytest.mark.parametrize(
    "result",
    [
        {"note": "untouched", "candidates_emitted": 0, "match_count": 3},
        _ambiguous(),
        _ambiguous(candidates_emitted=4),
    ],
    ids=["not-ambiguous", "nothing-shed", "every-candidate-kept"],
)
def test_ambiguity_note_stands_when_nothing_was_cut(result):
    before = result["note"]
    tool_symbol._correct_ambiguity_note(result, None)
    assert result["note"] == before


# ---------------------------------------------------------------------------
# Omission refs
# ---------------------------------------------------------------------------


@pytest.fixture
def omission_stores(tmp_path, monkeypatch):
    """Repo store under ``tmp_path``, "home" store under ``tmp_path/home``.

    ``_resolve_omission_ref`` also consults the user-level store, so home is
    redirected too; otherwise the test would read the developer's real one.
    """
    from repowise.core.distill import store as store_module

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(
        store_module, "default_store_path", lambda start=None: Path(start) / "omit" / "o.db"
    )
    return {"repo": tmp_path / "omit" / "o.db", "home": home / "omit" / "o.db"}


def _put(db_path: Path, content: str) -> str:
    from repowise.core.distill.store import OmissionStore

    store = OmissionStore(db_path)
    try:
        return store.put(content, source="test", original_tokens=40, kept_tokens=4)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_omission_query_that_matches_nothing_says_how_to_get_everything(
    setup_mcp, omission_stores
):
    from repowise.server.mcp_server import get_symbol

    ref = _put(omission_stores["repo"], "alpha\nbeta\n")
    result = await get_symbol(f"repowise#{ref}", query="zeta")
    assert result["kind"] == "omission"
    assert result["query"] == "zeta"
    assert not result["content"]
    assert result["note"].startswith("No lines matched the query")
    assert "created_at" in result


@pytest.mark.asyncio
async def test_a_corrupt_repo_store_falls_through_to_the_home_store(setup_mcp, omission_stores):
    """Recovery must never make get_symbol fail; an unreadable store is just a miss."""
    from repowise.server.mcp_server import get_symbol

    ref = _put(omission_stores["home"], "served from home\n")
    omission_stores["repo"].parent.mkdir(parents=True, exist_ok=True)
    omission_stores["repo"].write_bytes(b"this is not a sqlite database" * 64)

    result = await get_symbol(f"repowise#{ref}")
    assert "error" not in result
    assert result["content"] == "served from home\n"


# ---------------------------------------------------------------------------
# Range reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_range_read_on_a_missing_file_is_a_named_error(setup_mcp):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("pkg/ghost.py:1-3")
    assert result["error"] == "File could not be read: 'pkg/ghost.py'"
    assert result["symbol_id"] == "pkg/ghost.py:1-3"


@pytest.mark.asyncio
async def test_range_read_refuses_an_excluded_path(setup_mcp, tmp_path, monkeypatch):
    """An excluded file is not served even when it exists on disk."""
    from repowise.server.mcp_server import get_symbol

    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        tool_symbol, "is_excluded", lambda path, _spec: str(path).startswith("vendor/")
    )
    result = await get_symbol("vendor/lib.py:1-1")
    assert result["error"] == "'vendor/lib.py' is excluded from indexing."
    assert "source" not in result


# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_all_is_refused():
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("a.py::f", repo="all")
    assert "get_symbol" in result["error"]


@pytest.mark.asyncio
async def test_a_reference_carries_its_repository_in_workspace_mode(monkeypatch):
    """A structured reference is accepted whole: its id and its repository together."""
    seen: dict = {}

    async def _context(repo):
        seen["repo"] = repo
        raise LookupError("stop after resolution")

    monkeypatch.setattr(tool_symbol, "_is_workspace_mode", lambda: True)
    monkeypatch.setattr(tool_symbol, "_resolve_repo_context", _context)
    with pytest.raises(LookupError):
        await tool_symbol.get_symbol(reference={"id": "a.py:1-2", "repository": "web"})
    assert seen["repo"] == "web"


@pytest.mark.asyncio
async def test_an_explicit_repo_wins_over_the_references_repository(monkeypatch):
    seen: dict = {}

    async def _context(repo):
        seen["repo"] = repo
        raise LookupError("stop after resolution")

    monkeypatch.setattr(tool_symbol, "_is_workspace_mode", lambda: True)
    monkeypatch.setattr(tool_symbol, "_resolve_repo_context", _context)
    with pytest.raises(LookupError):
        await tool_symbol.get_symbol(
            reference={"id": "a.py:1-2", "repository": "web"}, repo="api"
        )
    assert seen["repo"] == "api"


@pytest.mark.asyncio
async def test_runaway_context_lines_are_clamped_to_fifty(setup_mcp, tmp_path):
    """``login`` is indexed at 20-40; 500 lines of context is served as 50."""
    from repowise.server.mcp_server import get_symbol

    lines = [f"# line {n}" for n in range(1, 201)]
    lines[19] = "    async def login(self, username: str, password: str) -> Token:"
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "auth" / "service.py").write_text("\n".join(lines) + "\n", "utf-8")

    result = await get_symbol("src/auth/service.py::login", context_lines=500)
    assert result["start_line"] == 1
    assert result["end_line"] == 90


# ---------------------------------------------------------------------------
# Helpers behind the dead-end recovery
# ---------------------------------------------------------------------------


def test_live_grep_caps_matches_and_handles_unreadable_or_nameless_input(tmp_path):
    (tmp_path / "consts.py").write_text(
        "\n".join(f"LIMIT_{i} = LIMIT" for i in range(20)) + "\n", encoding="utf-8"
    )
    matches = tool_symbol._live_grep_fallback(tmp_path, "consts.py", "LIMIT")
    assert len(matches) == tool_symbol._MAX_FALLBACK_MATCHES
    assert [m["line"] for m in matches] == list(range(1, 9))
    assert tool_symbol._live_grep_fallback(tmp_path, "missing.py", "LIMIT") == []
    assert tool_symbol._live_grep_fallback(tmp_path, "consts.py", "") == []


@pytest.mark.asyncio
async def test_suggestions_skip_excluded_paths_and_stop_at_five(session, repo_id, monkeypatch):
    for i in range(8):
        session.add(
            WikiSymbol(
                id=f"dup{i}",
                repository_id=repo_id,
                file_path=f"{'vendor' if i < 2 else 'src'}/m{i}.py",
                symbol_id=f"{'vendor' if i < 2 else 'src'}/m{i}.py::run",
                name="run",
                qualified_name="run",
                kind="function",
                signature="def run()",
                start_line=1,
                end_line=2,
                language="python",
            )
        )
    await session.flush()
    monkeypatch.setattr(
        tool_symbol, "is_excluded", lambda path, _spec: str(path).startswith("vendor/")
    )

    out = await tool_symbol._symbol_suggestions(session, repo_id, "wrong/path.py::run", None)
    assert len(out) == 5
    assert all(sid.startswith("src/") for sid in out)
    assert await tool_symbol._symbol_suggestions(session, repo_id, "wrong/path.py::", None) == []


# ---------------------------------------------------------------------------
# Ambiguous rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_candidate_in_a_vanished_file_is_listed_not_dropped(tmp_path):
    """A candidate whose file is gone is still named, with why it has no body."""
    (tmp_path / "b.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    def _row(path: str) -> WikiSymbol:
        return WikiSymbol(
            file_path=path,
            symbol_id=f"{path}::run",
            name="run",
            qualified_name="run",
            kind="function",
            signature="def run():",
            start_line=1,
            end_line=2,
        )

    ctx = SimpleNamespace(path=str(tmp_path), alias="", session_factory=None)
    result = await tool_symbol._render_ambiguous(
        [_row("a.py"), _row("b.py")], "run", ctx, None, 0.0, 0
    )
    assert result["match_count"] == 2
    assert [c["file"] for c in result["candidates"]] == ["b.py"]
    assert result["not_rendered"] == [
        {
            "symbol_id": "a.py::run",
            "file": "a.py",
            "name": "run",
            "kind": "function",
            "qualified_name": "run",
            "signature": "def run():",
            "note": "source file could not be read",
        }
    ]
    assert "not_rendered" in result["note"]
