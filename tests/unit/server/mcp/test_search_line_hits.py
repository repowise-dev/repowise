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


def _importer(session, rid: str, rel: str, target: str, names: str, language: str) -> None:
    session.add_all(
        [
            GraphNode(
                id=f"lh_file_{rel}",
                repository_id=rid,
                node_id=rel,
                node_type="file",
                language=language,
                created_at=_NOW,
            ),
            GraphEdge(
                id=f"lh_imp_{rel}",
                repository_id=rid,
                source_node_id=rel,
                target_node_id=target,
                edge_type="imports",
                imported_names_json=names,
                confidence=1.0,
                created_at=_NOW,
            ),
        ]
    )


@pytest.mark.asyncio
async def test_mock_heavy_python_test_collapses_into_recoverable_counts(tree, session, setup_mcp):
    (tree / "tests").mkdir()
    (tree / "tests" / "test_widget.py").write_text(
        "from pkg.catalog import backend_of\n"
        + "".join(f"mock.patch('pkg.catalog.backend_of', side_effect={i})\n" for i in range(20)),
        encoding="utf-8",
    )
    _importer(session, setup_mcp, "tests/test_widget.py", "pkg/catalog.py", '["backend_of"]', "python")
    await session.commit()

    out = await search_codebase("backend_of", mode="symbol")

    cap = _edit_sites.MAX_REFERENCES_PER_FILE
    assert _rows(out) == [
        ("pkg/catalog.py", 4, "definition"),
        ("app/widget.py", 1, "import"),
        ("app/widget.py", 6, "call"),
        ("tests/test_widget.py", 1, "import"),
        *(("tests/test_widget.py", n, "reference") for n in range(2, 2 + cap)),
    ]
    assert out["lines_omitted_by_file"] == {"tests/test_widget.py": 20 - cap}
    assert out["complete"] is False
    assert any("lines_omitted_by_file" in r for r in out["reasons"])
    assert out["_meta"]["omitted"]["refs"]


@pytest.mark.asyncio
async def test_mock_heavy_typescript_test_sorts_after_the_real_user(tree, session, setup_mcp):
    files = {
        "web/a.ts": "export function pick(x: number): number { return x }\n",
        "web/a.test.ts": "import { pick } from './a'\n" + "vi.mocked(pick).mockReturnValue(1)\n" * 30,
        "web/b.ts": "import { pick } from './a'\nexport const run = () => [pick]\n",
    }
    for rel, text in files.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text, encoding="utf-8")
    session.add(
        GraphNode(
            id="lh_ts_sym",
            repository_id=setup_mcp,
            node_id="web/a.ts::pick",
            node_type="symbol",
            name="pick",
            file_path="web/a.ts",
            kind="function",
            language="typescript",
            start_line=1,
            end_line=1,
            created_at=_NOW,
        )
    )
    for rel in ("web/a.test.ts", "web/b.ts"):
        _importer(session, setup_mcp, rel, "web/a.ts", '["pick"]', "typescript")
    await session.commit()

    out = await search_codebase("pick", mode="symbol")

    paths = [p for p, _n, _k in _rows(out)]
    assert ("web/b.ts", 2, "reference") in _rows(out)
    assert paths.index("web/b.ts") < paths.index("web/a.test.ts")
    assert paths.count("web/a.test.ts") == 1 + _edit_sites.MAX_REFERENCES_PER_FILE
    assert out["lines_omitted_by_file"] == {
        "web/a.test.ts": 30 - _edit_sites.MAX_REFERENCES_PER_FILE
    }
    assert out["complete"] is False


@pytest.mark.asyncio
async def test_call_sites_are_never_collapsed(tree, session, setup_mcp):
    for i in range(4):
        rel = f"svc/user{i}.py"
        (tree / "svc").mkdir(exist_ok=True)
        (tree / rel).write_text(
            "from pkg.catalog import backend_of\n" + "backend_of(1)\n" * 6, encoding="utf-8"
        )
        _importer(session, setup_mcp, rel, "pkg/catalog.py", '["backend_of"]', "python")
        session.add(
            GraphEdge(
                id=f"lh_call_{i}",
                repository_id=setup_mcp,
                source_node_id=f"{rel}::run",
                target_node_id="pkg/catalog.py::backend_of",
                edge_type="calls",
                call_lines_json="[2, 3, 4, 5, 6, 7]",
                confidence=0.95,
                created_at=_NOW,
            )
        )
    await session.commit()

    out = await search_codebase("backend_of", mode="symbol")

    calls = [r for r in _rows(out) if r[2] == "call" and r[0].startswith("svc/")]
    assert len(calls) == 4 * 6
    assert out["complete"] is True
    assert "lines_omitted_by_file" not in out
    assert "omitted" not in out["_meta"]


def test_literal_rows_are_not_collapsed():
    rows = [{"path": "a.py", "line": n, "kind": "match", "text": "x"} for n in range(5)]
    assert _edit_sites._cap_references_per_file(rows) == rows


async def _mocking_tests(tree, session, rid, target, name, ext, language, n=8):
    """*n* test files that each import *name* and mock it six times."""
    head = f"from pkg.catalog import {name}\n" if ext == "py" else f"import {{ {name} }} from '../web/a'\n"
    for i in range(n):
        rel = f"tests/test_user{i}.{ext}" if ext == "py" else f"tests/user{i}.test.{ext}"
        (tree / "tests").mkdir(exist_ok=True)
        (tree / rel).write_text(head + f"mock({name})\n" * 6, encoding="utf-8")
        _importer(session, rid, rel, target, f'["{name}"]', language)
    await session.commit()


def _recording_collector(monkeypatch):
    added: list = []

    class Recording(_line_hits.OmissionCollector):
        def add(self, label, rows, *a, **kw):
            added.extend(rows)
            return super().add(label, rows, *a, **kw)

    monkeypatch.setattr(_line_hits, "OmissionCollector", Recording)
    return added


def _assert_mixed_bound(out, hidden, n_files):
    cap, top = _line_hits.MAX_LINES_MIXED, _line_hits.MAX_OMITTED_FILES
    assert len(out["lines"]) == cap
    assert out["complete"] is False
    assert _line_hits.OVER_LINES_MIXED in out["reasons"]
    assert out["_meta"]["omitted"]["refs"]
    by_file = out["lines_omitted_by_file"]
    assert len(by_file) == top
    ranks = [_line_hits._MIXED_KIND_ORDER[r["kind"]] for r in out["lines"]]
    assert ranks == sorted(ranks)
    assert out["lines_omitted_by_file_total"] == n_files
    assert list(by_file.values()) == sorted(by_file.values(), reverse=True)
    # Everything not shown is in the omission chunk, nothing twice.
    shown = {(r["path"], r["line"]) for r in out["lines"]}
    assert not shown & {(r["path"], r["line"]) for r in hidden}
    assert all(by_file[p] == sum(r["path"] == p for r in hidden) for p in by_file)


@pytest.mark.asyncio
async def test_mixed_python_query_bounds_lines_and_omitted_files(tree, session, setup_mcp, monkeypatch):
    await _mocking_tests(tree, session, setup_mcp, "pkg/catalog.py", "backend_of", "py", "python")
    hidden = _recording_collector(monkeypatch)

    out = await search_codebase("where backend_of is mocked", mode="hybrid")

    # Calls before imports, production before test within each kind.
    assert _rows(out)[:4] == [
        ("pkg/catalog.py", 4, "definition"),
        ("app/widget.py", 6, "call"),
        ("app/widget.py", 1, "import"),
        ("tests/test_user0.py", 1, "import"),
    ]
    _assert_mixed_bound(out, hidden, n_files=8)
    assert len(out["lines"]) + len(hidden) == 3 + 8 * 7


@pytest.mark.asyncio
async def test_mixed_typescript_query_bounds_lines_and_omitted_files(
    tree, session, setup_mcp, monkeypatch
):
    (tree / "web").mkdir()
    (tree / "web" / "a.ts").write_text(
        "export function pickModel(x: number): number { return x }\n", encoding="utf-8"
    )
    session.add(
        GraphNode(
            id="lh_ts_mixed",
            repository_id=setup_mcp,
            node_id="web/a.ts::pickModel",
            node_type="symbol",
            name="pickModel",
            file_path="web/a.ts",
            kind="function",
            language="typescript",
            start_line=1,
            end_line=1,
            created_at=_NOW,
        )
    )
    await _mocking_tests(tree, session, setup_mcp, "web/a.ts", "pickModel", "ts", "typescript", n=7)
    hidden = _recording_collector(monkeypatch)

    out = await search_codebase("pickModel mock setup", mode="hybrid")

    assert out["lines"][0]["path"] == "web/a.ts"
    _assert_mixed_bound(out, hidden, n_files=7)


@pytest.mark.asyncio
async def test_single_name_keeps_the_full_cap_and_every_omitted_file(tree, session, setup_mcp):
    await _mocking_tests(tree, session, setup_mcp, "pkg/catalog.py", "backend_of", "py", "python")

    out = await search_codebase("backend_of", mode="symbol")

    cap = _edit_sites.MAX_REFERENCES_PER_FILE
    assert len(out["lines"]) == 3 + 8 * (1 + cap)
    assert len(out["lines_omitted_by_file"]) == 8
    assert "lines_omitted_by_file_total" not in out
    assert not any(r == _line_hits.OVER_LINES_MIXED for r in out["reasons"])


_SCAN_TREE = {
    "docs/guide.md": "Set `retryLimit` in the config.\n",
    "docs/api/retry.md": "retryLimit: the number of retries\n",
    "src/core/retry.ts": "export const retryLimit = 3;\nuse(retryLimit);\nuse($retryLimit);\n",
    "src/net/client.py": "retryLimit = 5\nself.retryLimit_old = 1\nprint(retryLimit)\n",
    "src/my dir/sub/limits.go": "var retryLimit = 4\nfunc f() { g(retryLimit) }\n",
    "tests/test_retry.py": "assert retryLimit == 3\n",
    "README.md": "retryLimit.\n",
}


@pytest.fixture
def scan_repo(tmp_path):
    for rel, text in _SCAN_TREE.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "docs", "src"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "seed"], check=True)
    # tests/ and README.md stay untracked: git grep must still read them.
    return tmp_path


def _python_only(monkeypatch):
    monkeypatch.setattr(_line_hits, "_files_holding", lambda _root, _needle: None)


def test_git_grep_and_python_scan_serve_the_same_rows(scan_repo, monkeypatch):
    held = _line_hits._files_holding(scan_repo, "retryLimit")
    assert held is not None and "src/my dir/sub/limits.go" in held
    assert "tests/test_retry.py" in held
    via_git = _line_hits._scan_listed(scan_repo, "retryLimit", True)
    _python_only(monkeypatch)
    via_python = _line_hits._scan_listed(scan_repo, "retryLimit", True)

    assert via_git == via_python
    rows, reasons = via_git
    assert reasons == []
    found = {(r["path"], r["line"], r["kind"]) for r in rows}
    # Word-bounded on both paths: $retryLimit and retryLimit_old are other names.
    assert ("src/core/retry.ts", 3, "match") not in found
    assert ("src/net/client.py", 2, "match") not in found
    assert {
        ("src/core/retry.ts", 1, "definition"),
        ("src/core/retry.ts", 2, "match"),
        ("src/net/client.py", 1, "definition"),
        ("src/my dir/sub/limits.go", 1, "definition"),
        ("tests/test_retry.py", 1, "match"),
        ("docs/guide.md", 1, "match"),
    } <= found


def test_git_grep_paths_are_relative_to_a_nested_root(scan_repo, monkeypatch):
    root = scan_repo / "src"
    via_git = _line_hits._scan_listed(root, "retryLimit", True)
    _python_only(monkeypatch)
    assert via_git == _line_hits._scan_listed(root, "retryLimit", True)
    assert {r["path"] for r in via_git[0]} == {
        "core/retry.ts",
        "net/client.py",
        "my dir/sub/limits.go",
    }


def test_byte_budget_reads_source_before_tests_and_docs(scan_repo, monkeypatch):
    _python_only(monkeypatch)
    sizes = sorted((scan_repo / rel).stat().st_size for rel in _SCAN_TREE if rel.startswith("src/"))
    monkeypatch.setattr(_line_hits, "MAX_SCAN_BYTES", sum(sizes))

    rows, reasons = _line_hits._scan_listed(scan_repo, "retryLimit", True)

    assert {r["path"].split("/")[0] for r in rows} == {"src"}
    assert reasons == [f"over 0 MB: scanned 3 of {len(_SCAN_TREE)} files, source first"]


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError("git"), subprocess.TimeoutExpired("git", 5)],
    ids=["git-missing", "timeout"],
)
def test_git_failure_falls_back_to_the_python_scan(scan_repo, monkeypatch, failure):
    expected = _line_hits._scan_listed(scan_repo, "retryLimit", True)
    real_run = subprocess.run

    def broken(cmd, *args, **kwargs):
        if "grep" in cmd or isinstance(failure, FileNotFoundError):
            raise failure
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(_line_hits.subprocess, "run", broken)

    assert _line_hits._files_holding(scan_repo, "retryLimit") is None
    assert _line_hits._scan_listed(scan_repo, "retryLimit", True) == expected


def test_token_nowhere_in_the_repo_is_complete_and_empty(scan_repo):
    assert _line_hits._files_holding(scan_repo, "noSuchTokenAnywhere") == []
    assert _line_hits._scan_listed(scan_repo, "noSuchTokenAnywhere", True) == ([], [])


@pytest.mark.parametrize("needle", ["a\nb", "a\0b", "-e", "café"])
def test_needles_git_cannot_take_as_one_argument_use_the_python_scan(scan_repo, needle):
    assert _line_hits._files_holding(scan_repo, needle) is None
