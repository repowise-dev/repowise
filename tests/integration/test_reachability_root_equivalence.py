"""Dead code reads ``is_reachability_root`` exactly as it read the single flag.

Before the split one ``is_entry_point`` flag both named entry points and exempted
files from dead code. The legacy graph below rebuilds that world: every root is
stamped an entry point and the root flag is dropped. The analyzer must report
the same findings on both graphs, whatever the entry flag means now.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.entry_candidacy import is_reachability_root
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _graph(repo: Path):
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _legacy(graph):
    legacy = graph.copy()
    for _node, data in legacy.nodes(data=True):
        data["is_entry_point"] = is_reachability_root(data)
        data.pop("is_reachability_root", None)
    return legacy


def _findings(graph) -> list[tuple]:
    report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze({"min_confidence": 0.0})
    return sorted(
        (f.kind.value, f.file_path, f.symbol_name or "", f.confidence) for f in report.findings
    )


@pytest.mark.parametrize("fixture", ["sample_repo", "ts_sample", "go_sample"])
def test_dead_code_unchanged_by_root_split(fixture: str) -> None:
    graph = _graph(FIXTURES / fixture)
    findings = _findings(graph)
    assert findings, "fixture should produce some findings"
    assert findings == _findings(_legacy(graph))
