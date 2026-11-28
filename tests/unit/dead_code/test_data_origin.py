"""Code a data folder holds is read by path, so dead-code analysis never judges it.

``backend/data/problems/<id>/solutions/`` holds a reference solution per
language beside per-problem JSON; a pipeline reads them with ``open()``.
``code_origin`` calls them ``data``, and no finding is raised on them.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser


def _report(root: Path):
    parser = ASTParser()
    builder = GraphBuilder(repo_path=root)
    source_map: dict[str, bytes] = {}
    parsed_files = {}
    for fi in FileTraverser(root).traverse():
        source = Path(fi.abs_path).read_bytes()
        source_map[fi.path] = source
        parsed = parser.parse_file(fi, source)
        parsed_files[fi.path] = parsed
        builder.add_file(parsed)
    builder.set_source_map(source_map)
    return DeadCodeAnalyzer(
        builder.build(),
        {},
        parsed_files=parsed_files,
        source_map=source_map,
        repo_root=root,
    ).analyze({"min_confidence": 0.0})


def test_a_solution_bank_raises_no_finding(tmp_path) -> None:
    files = {"backend/app.py": "def main():\n    return 1\n", "backend/orphan.py": "def gone():\n    pass\n"}
    for i in range(4):
        item = f"backend/data/problems/p{i}"
        files |= {f"{item}/case{n}.json": "{}" for n in range(12)}
        files[f"{item}/solutions/python.py"] = "class Solution:\n    def solve(self):\n        return 1\n"
        files[f"{item}/solutions/Solution.java"] = "public class Solution { public int solve() { return 1; } }\n"
        files[f"{item}/solutions/go.go"] = "package main\n\nfunc Solve() int { return 1 }\n"
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    flagged = {f.file_path for f in _report(tmp_path).findings}

    assert not any(path.startswith("backend/data/") for path in flagged)
    assert "backend/orphan.py" in flagged
