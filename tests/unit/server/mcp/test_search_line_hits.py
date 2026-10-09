"""search_codebase returns the source lines an identifier or literal query names."""

from __future__ import annotations

import inspect
import subprocess
from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.mcp_server import _edit_sites, _line_hits, _meta
from repowise.server.mcp_server._budget import enforce_response_budget
from repowise.server.mcp_server.tool_search import search_codebase

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)

_FILES = {
    "pkg/catalog.py": "PORT = 47821\nGREETING = 'hello there'\n\ndef backend_of(name):\n    return name\n",
    "app/widget.py": (
        "from pkg.catalog import backend_of\n"
        "\n"
        "def run():\n"
        "    connect(47821)\n"
        "    print('hello there')\n"
        "    return backend_of('m')\n"
        "x = 147821\n"
    ),
}


def _dirty(monkeypatch, paths: frozenset[str]) -> None:
    for module in (_edit_sites, _meta):
        monkeypatch.setattr(module, "_working_tree_dirty_paths", lambda _p: paths)


@pytest.fixture
async def tree(setup_mcp, session, tmp_path, monkeypatch):
    _dirty(monkeypatch, frozenset())
    for rel, text in _FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    # Committed but never indexed: the scan must still read it.
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "config.yaml").write_text("server:\n  port: 47821\n", encoding="utf-8")
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "pkg", "app", "deploy"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "seed"], check=True)
    rid = setup_mcp
    session.add_all(
        [
            *(
                GraphNode(
                    id=f"lh_file_{i}",
                    repository_id=rid,
                    node_id=rel,
                    node_type="file",
                    language="python",
                    created_at=_NOW,
                )
                for i, rel in enumerate(_FILES)
            ),
            GraphNode(
                id="lh_sym",
                repository_id=rid,
                node_id="pkg/catalog.py::backend_of",
                node_type="symbol",
                name="backend_of",
                file_path="pkg/catalog.py",
                kind="function",
                language="python",
                start_line=4,
                end_line=5,
                created_at=_NOW,
            ),
            GraphEdge(
                id="lh_e1",
                repository_id=rid,
                source_node_id="app/widget.py",
                target_node_id="pkg/catalog.py",
                edge_type="imports",
                imported_names_json='["backend_of"]',
                confidence=1.0,
                created_at=_NOW,
            ),
            GraphEdge(
                id="lh_e2",
                repository_id=rid,
                source_node_id="app/widget.py::run",
                target_node_id="pkg/catalog.py::backend_of",
                edge_type="calls",
                call_lines_json="[6]",
                confidence=0.95,
                created_at=_NOW,
            ),
        ]
    )
    await session.commit()
    return tmp_path


def _rows(out):
    return [(r["path"], r["line"], r["kind"]) for r in out["lines"]]


@pytest.mark.asyncio
async def test_identifier_query_returns_its_edit_set(tree):
    out = await search_codebase("backend_of", mode="symbol")

    assert _rows(out) == [
        ("pkg/catalog.py", 4, "definition"),
        ("app/widget.py", 1, "import"),
        ("app/widget.py", 6, "call"),
    ]
    assert out["complete"] is True
    assert "reasons" not in out
    assert set(out["lines"][0]) == {"path", "line", "kind", "text"}


@pytest.mark.asyncio
async def test_literal_number_is_scanned_with_definition_first(tree):
    out = await search_codebase("47821")

    # Word-bounded: 147821 is not a match. deploy/config.yaml is committed but
    # never indexed, and is read all the same.
    assert _rows(out) == [
        ("deploy/config.yaml", 2, "definition"),
        ("pkg/catalog.py", 1, "definition"),
        ("app/widget.py", 4, "match"),
    ]
    assert out["lines"][1]["text"] == "PORT = 47821"
    assert out["complete"] is True


@pytest.mark.asyncio
async def test_quoted_string_is_scanned_as_a_substring(tree):
    out = await search_codebase('"hello there"')

    assert _rows(out) == [("pkg/catalog.py", 2, "definition"), ("app/widget.py", 5, "match")]
    assert out["complete"] is True


@pytest.mark.asyncio
async def test_cap_hit_marks_lines_incomplete(tree, monkeypatch):
    monkeypatch.setattr(_line_hits, "MAX_LINES_PER_FILE", 0)

    out = await search_codebase("47821")

    assert out["lines"] == []
    assert out["complete"] is False
    assert any("over 0 lines" in r for r in out["reasons"])


@pytest.mark.asyncio
async def test_prose_query_gets_no_lines(tree):
    out = await search_codebase("how is the connection port chosen")

    assert "lines" not in out
    assert "complete" not in out


@pytest.mark.asyncio
async def test_uncommitted_unindexed_file_is_scanned(tree):
    (tree / "app" / "new.py").write_text("retry(47821)\n", encoding="utf-8")

    out = await search_codebase("47821")

    assert ("app/new.py", 1, "match") in _rows(out)
    assert out["complete"] is True


@pytest.mark.asyncio
async def test_binary_holding_the_token_and_undecodable_text_are_reported(tree):
    (tree / "logo.png").write_bytes(b"\x89PNG\x00\x00none")
    out = await search_codebase("47821")
    assert out["complete"] is True

    (tree / "logo.png").write_bytes(b"\x89PNG\x00\x0047821")
    out = await search_codebase("47821")
    assert out["complete"] is False
    assert "1 binary files contain the token" in out["reasons"]

    (tree / "logo.png").unlink()
    (tree / "notes.txt").write_bytes("café 47821".encode("latin-1"))
    out = await search_codebase("café")
    assert out["complete"] is False
    assert "1 non-UTF-8 text files not scanned" in out["reasons"]


@pytest.mark.asyncio
async def test_explicit_excludes_make_the_scan_incomplete(tree):
    (tree / ".repowise").mkdir(exist_ok=True)
    (tree / ".repowise" / "config.yaml").write_text(
        "exclude_patterns:\n  - deploy/\n", encoding="utf-8"
    )

    out = await search_codebase("47821")

    assert "deploy/config.yaml" not in {r["path"] for r in out["lines"]}
    assert out["complete"] is False
    assert "excluded paths not scanned" in out["reasons"]


@pytest.mark.asyncio
async def test_too_many_exact_symbols_ask_to_narrow(tree, session):
    rid = (await session.get(GraphNode, "lh_sym")).repository_id
    session.add_all(
        GraphNode(
            id=f"lh_dup_{i}",
            repository_id=rid,
            node_id=f"pkg/m{i}.py::backend_of",
            node_type="symbol",
            name="backend_of",
            file_path=f"pkg/m{i}.py",
            kind="function",
            language="python",
            start_line=1,
            end_line=2,
            created_at=_NOW,
        )
        for i in range(3)
    )
    await session.commit()

    out = await search_codebase("backend_of", mode="symbol")

    assert out["lines"] == []
    assert out["complete"] is False
    assert out["reasons"] == ["4 symbols match; narrow the query"]


def test_budget_dropping_lines_is_never_complete():
    row = {"path": "src/a.py", "line": 1, "kind": "match", "text": "x" * 160}
    result = {
        "results": [],
        "mode": "symbol",
        "lines": [dict(row, line=i) for i in range(400)],
        "complete": True,
        "_meta": {},
    }

    out = enforce_response_budget(
        "search_codebase",
        result,
        signature=inspect.signature(search_codebase),
        args=(),
        kwargs={"query": "x"},
    )

    assert "lines" not in out
    assert out["complete"] is False
    assert "lines cut to fit the response budget" in out["reasons"]


def test_lines_gone_without_a_total_stamp_still_flip_complete():
    result = {"results": [], "complete": True, "_meta": {}}

    _line_hits._lines_after_budget(result)

    assert result["complete"] is False
    assert result["reasons"] == ["lines cut to fit the response budget"]


def test_oversized_binary_is_streamed_for_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(_line_hits, "_MAX_FILE_BYTES", 100)
    (tmp_path / "blob.bin").write_bytes(b"\x00" * 300)
    assert _line_hits.scan_literal(tmp_path, ["blob.bin"], "47821", True) == ([], [])

    (tmp_path / "blob.bin").write_bytes(b"\x00" * 300 + b"47821")
    _, reasons = _line_hits.scan_literal(tmp_path, ["blob.bin"], "47821", True)
    assert reasons == ["1 binary files contain the token"]


def test_stream_finds_a_needle_split_across_chunks(tmp_path):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\x00" * 6 + b"47821" + b"\x00" * 6)
    with open(path, "rb") as fh:
        assert _line_hits._stream_contains(fh, b"47821", chunk=8)
        assert not _line_hits._stream_contains(fh, b"99999", chunk=8)


def test_paths_that_leave_the_root_are_refused(tmp_path):
    assert not _line_hits._inside("../secret")
    assert not _line_hits._inside(str(tmp_path / "a.py"))
    assert not _line_hits._inside("/etc/passwd")
    assert _line_hits._inside("src/a.py")
    _, reasons = _line_hits.scan_literal(tmp_path, ["../x.py"], "x", True)
    assert reasons == ["unreadable: ../x.py"]


def test_definition_patterns():
    rows, reasons = _line_hits.scan_literal(
        _line_hits.Path(__file__).parent, [], "x", True
    )
    assert rows == [] and reasons == []
    word = _line_hits._definition_re("MAX_SIZE", True)
    assert word.search("MAX_SIZE = 3")
    assert word.search("  max_size: 3") is None
    assert word.search("export const MAX_SIZE = 3")
    assert word.search("if MAX_SIZE == 3:") is None
    value = _line_hits._definition_re("47821", True)
    assert value.search('  "port": 47821,')
    assert value.search("const PORT: number = 47821;")
    assert value.search("connect(port=47821)") is None
