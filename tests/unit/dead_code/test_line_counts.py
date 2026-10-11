"""Dead-code line counts are counted, never estimated.

Files used to report ``symbol_count * 10`` lines and symbols ``end - start``,
so a one-line symbol reported 0. Spans are inclusive and file counts come from
the source; a count that cannot be made is ``None`` with an evidence line.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph, _old_date

_PLAIN = {"is_entry_point": False, "is_test": False, "is_api_contract": False}
_STALE = {"commit_count_90d": 0, "last_commit_at": _old_date(days=400), "age_days": 500}


def _file(symbol_count: int = 0, symbols: list | None = None) -> dict:
    return {**_PLAIN, "symbol_count": symbol_count, "symbols": symbols or []}


def _unreachable(g, **kwargs):
    report = DeadCodeAnalyzer(g, git_meta_map={p: _STALE for p in g.nodes}, **kwargs).analyze(
        {"detect_unused_exports": False, "detect_zombie_packages": False, "min_confidence": 0.0}
    )
    return {f.file_path: f for f in report.findings}, report


def test_one_line_symbol_counts_one_line():
    g = _build_graph(
        nodes={
            "pkg/utils.py": _file(
                1,
                [
                    {
                        "name": "helper",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 7,
                        "end_line": 7,
                    }
                ],
            ),
            "pkg/main.py": {**_file(), "is_entry_point": True},
        },
        edges=[("pkg/main.py", "pkg/utils.py", {"imported_names": ["other"]})],
    )
    report = DeadCodeAnalyzer(g, git_meta_map={}).analyze(
        {"detect_unreachable_files": False, "detect_zombie_packages": False}
    )
    finding = next(f for f in report.findings if f.symbol_name == "helper")
    assert finding.kind == DeadCodeKind.UNUSED_EXPORT
    assert (finding.start_line, finding.end_line, finding.lines) == (7, 7, 1)


def test_nineteen_line_file_counts_nineteen_lines():
    # symbol_count would have estimated 50 lines; the source says 19, with or
    # without a trailing newline.
    source = "\n".join(f"x{i} = {i}" for i in range(19)).encode()
    g = _build_graph(nodes={"pkg/a.py": _file(5), "pkg/b.py": _file(5)})
    by_path, report = _unreachable(g, source_map={"pkg/a.py": source + b"\n", "pkg/b.py": source})
    assert by_path["pkg/a.py"].lines == 19
    assert by_path["pkg/b.py"].lines == 19
    # Whole files are review-only, so none of their lines count as deletable.
    assert report.deletable_lines == 0


def test_file_counted_from_disk_without_source_map(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("a = 1\nb = 2\nc = 3\n")
    g = _build_graph(nodes={"pkg/a.py": _file(9)})
    by_path, _ = _unreachable(g, repo_root=tmp_path)
    assert by_path["pkg/a.py"].lines == 3


def test_unreadable_file_is_unknown_not_guessed():
    g = _build_graph(nodes={"pkg/gone.py": _file(9), "pkg/a.py": _file(1)})
    by_path, _ = _unreachable(g, source_map={"pkg/a.py": b"x = 1\n"})
    gone = by_path["pkg/gone.py"]
    assert gone.lines is None
    assert "Line count unavailable: source was not read" in gone.evidence
    assert by_path["pkg/a.py"].lines == 1


def test_zombie_package_sums_real_file_lines(tmp_path: Path):
    # The manifests that make each folder a package, read from the checkout.
    for pkg in ("pkgA", "pkgB"):
        (tmp_path / pkg).mkdir()
        (tmp_path / pkg / "pyproject.toml").write_text("[project]", encoding="utf-8")
    g = _build_graph(
        nodes={
            "pkgA/one.py": _file(10),
            "pkgA/two.py": _file(10),
            "pkgB/three.py": _file(10),
        }
    )
    source_map = {"pkgA/one.py": b"a\nb\n", "pkgA/two.py": b"c\n", "pkgB/three.py": b"d\n"}
    report = DeadCodeAnalyzer(
        g, git_meta_map={}, source_map=source_map, repo_root=tmp_path
    ).analyze({"detect_unreachable_files": False, "detect_unused_exports": False})
    zombie = next(
        f
        for f in report.findings
        if f.kind == DeadCodeKind.ZOMBIE_PACKAGE and f.file_path == "pkgA"
    )
    assert zombie.lines == 3
