"""What a Read would have cost: the gutter, the rangeless window, the trust boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.distill.budget import (
    READ_DEFAULT_LINES,
    READ_GUTTER_CHARS,
    estimate_read_tokens,
    rangeless_read,
)
from repowise.server.mcp_server._savings import interaction
from repowise.server.mcp_server._savings.counterfactual import ANSWER_SEARCH_FLOOR
from repowise.server.mcp_server._savings.reads import file_read_tokens, read_tokens


def test_the_gutter_is_seven_chars_a_line_and_only_when_numbered() -> None:
    text = "a = 1\nb = 2\n"
    assert estimate_read_tokens(text, numbered=False) == len(text) // 4
    assert estimate_read_tokens(text, numbered=True) == (len(text) + 2 * READ_GUTTER_CHARS) // 4


def test_a_rangeless_read_stops_at_the_default_window() -> None:
    long = "x\n" * (READ_DEFAULT_LINES + 50)
    assert rangeless_read(long) == "x\n" * READ_DEFAULT_LINES
    assert rangeless_read("short\n") == "short\n"


def test_only_claude_code_reads_are_numbered() -> None:
    text = "line\n" * 100
    bare = len(text) // 4
    assert read_tokens(text) == bare  # no interaction open
    with interaction.begin("get_symbol") as live:
        live.agent = "claude_code"
        assert read_tokens(text) == (len(text) + 100 * READ_GUTTER_CHARS) // 4
        live.agent = "codex"
        assert read_tokens(text) == bare


def test_file_reads_are_windowed_and_refuse_what_is_not_a_repo_text_file(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "big.py").write_text("y = 1\n" * (READ_DEFAULT_LINES + 500), encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02")
    (tmp_path / "outside.py").write_text("secret = 1\n", encoding="utf-8")

    assert file_read_tokens(root, "big.py") == len("y = 1\n" * READ_DEFAULT_LINES) // 4
    assert file_read_tokens(root, "missing.py") is None
    assert file_read_tokens(root, "blob.bin") is None
    assert file_read_tokens(root, "../outside.py") is None


def _repo_with(tmp_path, files: dict[str, str]):
    root = tmp_path / "repo"
    root.mkdir()
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    return root


async def test_get_answer_declares_the_real_read_of_what_it_cited(tmp_path) -> None:
    from repowise.server.mcp_server.tool_answer.projection import _declare_replaced_reads

    root = _repo_with(tmp_path, {"a.py": "a = 1\n" * 400})
    payload = {"answer": "x", "confidence": "high", "citations": ["a.py"], "_meta": {}}
    await _declare_replaced_reads(payload, root, None)
    assert payload["_meta"]["replaced_tokens"] == ANSWER_SEARCH_FLOOR + file_read_tokens(root, "a.py")


async def test_get_context_declares_one_read_per_file_card(tmp_path) -> None:
    from repowise.server.mcp_server.tool_context.context import _declare_replaced_reads

    root = _repo_with(tmp_path, {"a.py": "a = 1\n" * 900})
    response = {"targets": {"a.py": {"type": "file"}}, "_meta": {}}
    _declare_replaced_reads(response, root)
    assert response["_meta"]["replaced_tokens"] == file_read_tokens(root, "a.py")


class _Connection:
    """A weak-referenceable stand-in for one MCP client session."""


@pytest.fixture
def connect(monkeypatch):
    """Open a new client connection; the fixture starts with one open."""

    def _connect() -> None:
        session = _Connection()
        monkeypatch.setattr(interaction, "_context", lambda: SimpleNamespace(session=session))

    _connect()
    return _connect


@pytest.fixture
def get_symbol():
    """``get_symbol`` behind the layers a registered tool runs in."""
    from repowise.server.mcp_server import get_symbol as tool
    from repowise.server.mcp_server import tool_middleware

    return tool_middleware(tool)


@pytest.fixture
def get_symbol_baselines(monkeypatch, tmp_path) -> list[int | None]:
    """The baseline of every recorded ``get_symbol`` event, in call order."""
    from repowise.core.savings import recorder
    from repowise.server.mcp_server import _state

    baselines: list[int | None] = []
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path), raising=False)

    def _record(_repo_root, payload) -> bool:
        if payload["operation"] == "get_symbol":
            baselines.append(payload["baseline_input_tokens"])
        return True

    monkeypatch.setattr(recorder, "record_event", _record)
    return baselines


def _source(tmp_path, rel: str, lines: int = 400) -> int:
    (tmp_path / rel).write_bytes(b"value = 1\n" * lines)
    return file_read_tokens(tmp_path, rel)


async def test_fragments_of_one_file_credit_one_read(
    setup_mcp, tmp_path, connect, get_symbol, get_symbol_baselines
) -> None:
    full = _source(tmp_path, "a.py")
    await get_symbol("a.py:1-2")
    await get_symbol("a.py:3-4")
    await get_symbol("a.py:1-2")
    assert get_symbol_baselines == [full, None, None]


async def test_a_symbol_and_a_range_of_one_file_share_the_read(
    setup_mcp, tmp_path, session, connect, get_symbol, get_symbol_baselines
) -> None:
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository, WikiSymbol

    full = _source(tmp_path, "a.py")
    repo = (await session.execute(select(Repository))).scalars().first()
    session.add(
        WikiSymbol(
            id="read-credit-run",
            repository_id=repo.id,
            file_path="a.py",
            symbol_id="a.py::run",
            name="run",
            qualified_name="run",
            kind="function",
            signature="value = 1",
            start_line=1,
            end_line=1,
            language="python",
        )
    )
    await session.flush()

    await get_symbol("a.py::run")
    await get_symbol("a.py:3-4")
    assert get_symbol_baselines == [full, None]


async def test_each_file_credits_its_own_read(
    setup_mcp, tmp_path, connect, get_symbol, get_symbol_baselines
) -> None:
    first = _source(tmp_path, "a.py", 400)
    second = _source(tmp_path, "b.py", 500)
    await get_symbol("a.py:1-2")
    await get_symbol("b.py:1-2")
    assert get_symbol_baselines == [first, second]


async def test_an_edited_file_credits_a_new_read(
    setup_mcp, tmp_path, connect, get_symbol, get_symbol_baselines
) -> None:
    before = _source(tmp_path, "a.py", 400)
    await get_symbol("a.py:1-2")
    after = _source(tmp_path, "a.py", 300)
    await get_symbol("a.py:1-2")
    assert get_symbol_baselines == [before, after]


async def test_a_new_connection_credits_the_read_again(
    setup_mcp, tmp_path, connect, get_symbol, get_symbol_baselines
) -> None:
    full = _source(tmp_path, "a.py")
    await get_symbol("a.py:1-2")
    connect()
    await get_symbol("a.py:1-2")
    assert get_symbol_baselines == [full, full]


async def test_ambiguous_candidates_credit_only_unread_files(tmp_path, connect) -> None:
    from repowise.core.persistence.models import WikiSymbol
    from repowise.server.mcp_server import tool_symbol

    def _row(path: str) -> WikiSymbol:
        return WikiSymbol(
            file_path=path,
            symbol_id=f"{path}::run",
            name="run",
            qualified_name="run",
            kind="function",
            signature="value = 1",
            start_line=1,
            end_line=1,
        )

    first = _source(tmp_path, "a.py", 400)
    second = _source(tmp_path, "b.py", 500)
    ctx = SimpleNamespace(path=str(tmp_path), alias="", session_factory=None)

    async def _replaced(paths: list[str]) -> int | None:
        rows = [_row(path) for path in paths]
        result = await tool_symbol._render_ambiguous(rows, "run", ctx, None, 0.0, 0)
        return result["_meta"].get("replaced_tokens")

    assert await _replaced(["a.py"]) == first
    assert await _replaced(["a.py", "b.py"]) == second
    assert await _replaced(["a.py", "b.py"]) is None
