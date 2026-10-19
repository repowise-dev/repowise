"""Golden output of ``HealthAnalyzer.analyze`` on the checked-in sample corpora.

Pins per-file metrics and scores, every finding (biomarker type, severity,
lines, impact, details) with its public id, the repo KPIs, and which
refactoring suggestions came out, in the order the engine returns them. Two
corpora: the refactoring archetype trees and the per-language samples. Rewrite
with ``REPOWISE_REWRITE_GOLDEN=1`` only for an intended behaviour change.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.analysis.health.finding_identity import finding_public_id
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
GOLDEN = Path(__file__).parent / "golden" / "health_engine.json"
CORPORA = {
    "trees/gopkg": FIXTURES / "refactoring_corpus" / "trees" / "gopkg",
    "trees/orm": FIXTURES / "refactoring_corpus" / "trees" / "orm",
    "trees/tsapp": FIXTURES / "refactoring_corpus" / "trees" / "tsapp",
    "trees/web": FIXTURES / "refactoring_corpus" / "trees" / "web",
    "lang_samples": FIXTURES / "lang_samples",
}


def _copy_lf(src: Path, dst: Path) -> None:
    """Copy *src* to *dst* with LF line endings, as a Linux checkout has them."""
    for path in sorted(src.rglob("*")):
        if path.is_file():
            target = dst / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


def _analyze(root: Path) -> dict:
    # Unlike the refactoring loader, a parse failure fails the run: a file
    # skipped on one machine only would make the golden platform-dependent.
    parser = ASTParser()
    builder = GraphBuilder(root)
    parsed = []
    for info in FileTraverser(root).traverse():
        pf = parser.parse_file(info, Path(info.abs_path).read_bytes())
        builder.add_file(pf)
        parsed.append(pf)
    builder.build()
    report = HealthAnalyzer(
        builder.graph(), git_meta_map={}, parsed_files=parsed, repo_root=root
    ).analyze(None)
    findings = []
    for f in report.findings:
        row = dataclasses.asdict(f)
        row["severity"] = str(f.severity)
        row["id"] = finding_public_id(f)
        findings.append(row)
    payload = {
        "metrics": [dataclasses.asdict(m) for m in report.metrics],
        "findings": findings,
        "kpis": report.kpis,
        "repo_function_mod_p80": report.repo_function_mod_p80,
        "suggestions": [
            {
                key: getattr(s, key)
                for key in (
                    "refactoring_type",
                    "file_path",
                    "target_symbol",
                    "line_start",
                    "line_end",
                    "impact_delta",
                    "effort_bucket",
                    "confidence",
                    "source_biomarker",
                )
            }
            for s in report.refactoring_suggestions
        ],
    }
    # The temp root is the one volatile value; details may name it.
    text = json.dumps(payload, default=str)
    for spelling in {str(root), root.as_posix(), json.dumps(str(root))[1:-1]}:
        text = text.replace(spelling, "<root>")
    return json.loads(text)


def test_health_engine_matches_golden(tmp_path: Path) -> None:
    actual = {}
    for name, src in CORPORA.items():
        root = tmp_path / name.replace("/", "_")
        _copy_lf(src, root)
        actual[name] = _analyze(root)
    if os.environ.get("REPOWISE_REWRITE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name in CORPORA:
        assert actual[name] == expected[name], name
    assert actual.keys() == expected.keys()
