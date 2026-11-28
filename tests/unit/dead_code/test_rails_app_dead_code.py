"""Dead code on a small Rails app reports only the file nothing reaches.

``tests/fixtures/rails_app`` carries the shapes that made live Rails code read
as unreachable: Zeitwerk constant references, nested routes and
``scope module:``, a migration under ``db/migrate``, initializers, the
ActionCable connection and a channel, and an ``enterprise/`` overlay mixed in
with ``prepend_mod_with``. ``app/services/orphan_report_service.rb`` is the
one file nothing names.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.pipeline.incremental import build_repo_graph

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "rails_app"


def _copy_lf(src: Path, dst: Path) -> None:
    for path in sorted(src.rglob("*")):
        if path.is_file():
            target = dst / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


def test_only_the_orphan_is_unreachable(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _copy_lf(FIXTURE, root)
    _parsed, source_map, builder, _structure, _count = build_repo_graph(
        root, [], collect_sources=True, read_only=True
    )
    report = DeadCodeAnalyzer(
        builder.graph(),
        {},
        parsed_files=builder._parsed_files,
        source_map=source_map,
        repo_root=root,
        unindexed_source_files=builder.traversal_stats.unindexed_source_files(),
    ).analyze({"min_confidence": 0.0})

    unreachable = sorted(f.file_path for f in report.findings if f.kind.value == "unreachable_file")
    assert unreachable == ["app/services/orphan_report_service.rb"]
