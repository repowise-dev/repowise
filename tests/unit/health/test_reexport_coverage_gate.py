"""Coverage markers skip files that hold nothing but a docstring, imports and export lists."""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_FILES = {
    "pkg/__init__.py": '"""A package docstring and nothing else."""\n',
    "pkg/api/__init__.py": (
        "from __future__ import annotations\n\n"
        'from pkg.a import walk\nfrom pkg.b import scan\n\n__all__ = ["walk", "scan"]\n'
    ),
    "pkg/a.py": "def walk(paths):\n    return [p for p in paths if p]\n",
    "pkg/b.py": "def scan(paths):\n    return list(paths)\n",
    "pkg/consts.py": "LIMIT = 10\nNAMES = ('a', 'b')\n",
    "pkg/script.py": '"""Run."""\nimport sys\n\nsys.path.insert(0, "lib")\n',
    "pkg/run.py": '"""Doc\nmore."""\nprint("go")\n',
    "pkg/env.py": 'import os; os.environ["A"] = "1"\n',
    "pkg/sub/__init__.py": '"""Several\nlines of\npackage docs.\n"""\n\nfrom pkg.a import walk  # noqa: F401\n',
    "web/index.ts": "export * from './util';\nexport { helper } from './helper';\n",
    "web/util.ts": "export function util(x: number): number {\n  return x + 1;\n}\n",
}
_UNCOVERED = {"line_coverage_pct": 0.0, "total_coverable_lines": 60, "covered_lines": []}


def _coverage_markers(root: Path) -> dict[str, set[str]]:
    for rel, text in _FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    parser, gb, parsed = ASTParser(), GraphBuilder(), []
    for fi in sorted(FileTraverser(root).traverse(), key=lambda f: f.path):
        pf = parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        gb.add_file(pf)
        parsed.append(pf)
    gb.build()
    coverage = {rel: dict(_UNCOVERED) for rel in _FILES}
    report = HealthAnalyzer(gb.graph(), parsed_files=parsed, coverage_map=coverage).analyze()
    out: dict[str, set[str]] = {rel: set() for rel in _FILES}
    for f in report.findings:
        if f.biomarker_type in {"coverage_gradient", "coverage_gap"}:
            out[f.file_path].add(f.biomarker_type)
    return out


def test_reexport_and_docstring_only_files_carry_no_coverage_finding(tmp_path: Path) -> None:
    markers = _coverage_markers(tmp_path)

    assert markers["pkg/__init__.py"] == set()
    assert markers["pkg/api/__init__.py"] == set()
    assert markers["pkg/sub/__init__.py"] == set()
    assert markers["web/index.ts"] == set()


def test_files_that_declare_code_keep_their_coverage_findings(tmp_path: Path) -> None:
    markers = _coverage_markers(tmp_path)

    for rel in ("pkg/a.py", "pkg/consts.py", "pkg/script.py", "pkg/run.py", "pkg/env.py", "web/util.ts"):
        assert markers[rel] == {"coverage_gradient", "coverage_gap"}, rel
