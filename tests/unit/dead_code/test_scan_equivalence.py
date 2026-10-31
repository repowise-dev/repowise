"""The dead-code source scans and passes give the same answers as their slower forms.

The path-token regex skips match starts inside a word, the occurrence scans
read each distinct token once, and the file passes skip symbol nodes. Each is
checked here against the form it replaced.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.file_reachability import is_file_reachable
from repowise.core.analysis.dead_code.name_occurrences import (
    _PATH_TOKEN_RE,
    IDENTIFIER_RE,
    _targets_in,
    _targets_named_by,
    occurrence_files,
)
from repowise.core.analysis.dead_code.passes.unreachable_files import UnreachableFilePass
from repowise.core.analysis.dead_code.passes.unused_exports import UnusedExportPass
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from tests.unit.dead_code._helpers import _build_graph, _old_date

_REFERENCE_PATH_TOKEN_RE = re.compile(rb"[A-Za-z0-9_@~+\-]*[./][A-Za-z0-9_.@~+\-/]*")

_SAMPLES = [
    b"",
    b"plain words only with no separators at all",
    b"import pkg.mod.name; from ./rel/path.ts import x",
    b'"@scope/pkg/index.js" ~/home/file.sh a-b.c+d ..//.. ./. /abs',
    b"averyveryverylongidentifierwithoutanydotfollowedby.ext and/slash",
    b"tab\tsep.a\nnew/line.b\r\nx.y.z/w",
    "unicode café/naïve.py ü.b".encode(),
    b"trailing.dot. trailing/slash/ .leading /leading",
]


def _fuzz_blobs(count: int = 300) -> list[bytes]:
    rng = random.Random(1234)
    alphabet = b"ab_Z09-+@~./ \n\"'(){}:\x00\xc3\xa9"
    return [bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 200))) for _ in range(count)]


def test_path_token_regex_matches_the_reference_regex():
    for blob in _SAMPLES + _fuzz_blobs():
        assert _PATH_TOKEN_RE.findall(blob) == _REFERENCE_PATH_TOKEN_RE.findall(blob)


def test_targets_in_matches_a_per_match_scan():
    wanted = {"src/a.py": {"src/a.py"}, "src/a": {"src/a.py"}, "b.json": {"b.json"}}
    tails = {"a.py", "a", "b.json"}
    blob = b"run src/a.py then ./b.json and src/a.py again; src/a b.json"
    reference = {
        t
        for m in _REFERENCE_PATH_TOKEN_RE.finditer(blob)
        for t in _targets_named_by(m.group().decode("ascii"), wanted, tails, "")
    }
    assert _targets_in(blob, wanted, tails, "") == reference == {"src/a.py", "b.json"}


def test_occurrence_files_matches_a_per_match_scan():
    blobs = _SAMPLES + _fuzz_blobs()
    source = {f"f{i}.py": blob for i, blob in enumerate(blobs)}
    names = {b"words", b"pkg", b"abZ", b"leading", b"missing"}
    reference: dict[bytes, set[str]] = {}
    for path, blob in source.items():
        for m in IDENTIFIER_RE.finditer(blob):
            if m.group() in names:
                reference.setdefault(m.group(), set()).add(path)
    found = occurrence_files(source, names)
    assert found == reference
    assert list(found) == list(reference)


def test_unreachable_file_pass_judges_files_not_symbols():
    # A symbol with no in-edge at all is still not a file with no importer.
    symbol = {"name": "f", "kind": "function", "visibility": "public", "language": "python"}
    g = _build_graph({"lonely.py": {"symbols": [symbol]}})
    g.remove_edge("lonely.py", "lonely.py::f")
    meta = {"lonely.py": {"commit_count_90d": 0, "last_commit_at": _old_date(400)}}
    analyzer = DeadCodeAnalyzer(g, git_meta_map=meta)
    assert [f.file_path for f in UnreachableFilePass(analyzer).run(set())] == ["lonely.py"]
    report = analyzer.analyze({"detect_unused_exports": False, "detect_unused_internals": False})
    unreachable = [f.file_path for f in report.findings if f.kind is DeadCodeKind.UNREACHABLE_FILE]
    assert unreachable == ["lonely.py"]


def test_unused_export_pass_skips_symbol_nodes_but_judges_files():
    symbol = {"name": "f", "kind": "function", "visibility": "public", "language": "python"}
    g = _build_graph({"lonely.py": {"symbols": [symbol]}})
    meta = {"lonely.py": {"commit_count_90d": 0, "last_commit_at": _old_date(400)}}
    export_pass = UnusedExportPass(DeadCodeAnalyzer(g, git_meta_map=meta))
    assert export_pass._export_file_context("lonely.py::f", set()) is None
    assert export_pass._export_file_context("lonely.py", set()) is not None
    findings = export_pass.run((), set())
    assert [(f.file_path, f.symbol_name) for f in findings] == [("lonely.py", "f")]


_BUILT = {
    "pkg/a.py": "def used():\n    return 1\n\nclass Box:\n    def method(self):\n        return used()\n",
    "pkg/b.py": "from pkg.a import used\n\ndef _private():\n    return used()\n",
    "src/main.go": "package main\n\nfunc helper() int { return 1 }\n\nfunc main() { helper() }\n",
    "web/app.ts": "export function render(): number { return 1 }\nfunction local() { return 2 }\n",
}


def test_builder_symbols_never_reach_the_file_passes(tmp_path: Path):
    # Why skipping symbols changes no finding: in a built graph every symbol is
    # reached through its file's ``defines`` edge and defines nothing itself.
    for rel, text in _BUILT.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo_path=tmp_path)
    for fi in FileTraverser(tmp_path).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    graph = builder.build()
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
    symbols = [n for n, d in graph.nodes(data=True) if d.get("node_type") == "symbol"]
    assert len(symbols) > len(_BUILT)
    for node in symbols:
        assert is_file_reachable(node, graph)
        assert analyzer._defined_symbols(node) == []
