"""What a Read would have cost: the gutter, the rangeless window, the trust boundary."""

from __future__ import annotations

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
