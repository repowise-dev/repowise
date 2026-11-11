"""``analyze`` and ``analyze_async`` share every step after the walk, so on the
same input they return the same report."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_BRANCHY = """
import os


def walk(paths, flags):
    out = []
    for p in paths:
        if p.endswith(".py"):
            for line in open(p):
                if line.startswith("#") and flags.get("skip"):
                    continue
                elif "TODO" in line:
                    out.append(os.path.basename(p))
                else:
                    try:
                        out.append(int(line))
                    except ValueError:
                        pass
    return out
"""


def _parsed(root: Path) -> tuple[object, list]:
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "a.py").write_text(_BRANCHY, encoding="utf-8")
    (root / "pkg" / "b.py").write_text(_BRANCHY.replace("walk", "scan"), encoding="utf-8")
    (root / "pkg" / "c.py").write_text("from pkg.a import walk\n\n\ndef run():\n    return walk([], {})\n")
    parser, gb, parsed = ASTParser(), GraphBuilder(), []
    for fi in sorted(FileTraverser(root).traverse(), key=lambda f: f.path):
        pf = parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        gb.add_file(pf)
        parsed.append(pf)
    gb.build()
    return gb.graph(), parsed


def _comparable(report) -> dict:
    out = dataclasses.asdict(dataclasses.replace(report, execution_roles=None))
    out.pop("analyzed_at")
    return out


def test_sync_and_async_paths_return_the_same_report(tmp_path: Path) -> None:
    graph, parsed = _parsed(tmp_path)
    config = {"per_file_disabled": {"pkg/b.py": {"complex_method"}}}

    sync = HealthAnalyzer(graph, parsed_files=parsed).analyze(config)
    parallel = asyncio.run(HealthAnalyzer(graph, parsed_files=parsed).analyze_async(config))

    assert sync.findings, "the fixture should produce findings"
    assert _comparable(sync) == _comparable(parallel)
