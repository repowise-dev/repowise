"""Golden output of ``DeadCodeAnalyzer.analyze`` on the mixed-language sample repo.

Pins every finding field, the order findings come back in, and the report
totals, so a refactor of the analyzer cannot move a verdict unseen. The graph
is the one ``update`` builds (``build_repo_graph``). Rewrite with
``REPOWISE_REWRITE_GOLDEN=1`` only for an intended behaviour change.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.pipeline.incremental import build_repo_graph

GOLDEN = Path(__file__).parent / "golden" / "dead_code_sample_repo.json"


def _copy_lf(src: Path, dst: Path) -> None:
    """Copy *src* to *dst* with LF line endings, as a Linux checkout has them."""
    for path in sorted(src.rglob("*")):
        if path.is_file():
            target = dst / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


def _report_dict(report, root: Path) -> dict:
    def finding(f) -> dict:
        row = dataclasses.asdict(f)
        row["kind"] = f.kind.value
        row["last_commit_at"] = f.last_commit_at.isoformat() if f.last_commit_at else None
        return row

    # The temp root is the one volatile value; evidence may name it.
    text = json.dumps(
        {
            "total_findings": report.total_findings,
            "deletable_lines": report.deletable_lines,
            "confidence_summary": report.confidence_summary,
            "hidden_below_threshold": report.hidden_below_threshold,
            "findings": [finding(f) for f in report.findings],
        }
    )
    for spelling in {str(root), root.as_posix(), json.dumps(str(root))[1:-1]}:
        text = text.replace(spelling, "<root>")
    return json.loads(text)


def _analyze(tmp_path: Path, sample_repo_path: Path) -> dict:
    root = tmp_path / "repo"
    _copy_lf(sample_repo_path, root)
    _parsed, source_map, builder, _structure, _count = build_repo_graph(
        root, [], collect_sources=True, read_only=True
    )
    analyzer = DeadCodeAnalyzer(
        builder.graph(),
        {},
        parsed_files=builder._parsed_files,
        source_map=source_map,
        repo_root=root,
        unindexed_source_files=builder.traversal_stats.unindexed_source_files(),
        dotnet_index=getattr(builder, "dotnet_index", None),
    )
    steps: list[str] = []
    return {
        "all": _report_dict(analyzer.analyze({"min_confidence": 0.0}, on_step=steps.append), root),
        "steps": steps,
        # The default floor, and a whitelisted file with detectors switched off.
        "default": _report_dict(analyzer.analyze(), root),
        "narrowed": _report_dict(
            analyzer.analyze(
                {
                    "min_confidence": 0.0,
                    "whitelist": ["dead/unreachable_module.py"],
                    "detect_zombie_packages": False,
                    "detect_unused_internals": False,
                }
            ),
            root,
        ),
    }


def test_dead_code_matches_golden(tmp_path: Path, sample_repo_path: Path) -> None:
    actual = _analyze(tmp_path, sample_repo_path)
    if os.environ.get("REPOWISE_REWRITE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected
