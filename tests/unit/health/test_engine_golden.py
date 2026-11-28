"""Golden output of ``HealthAnalyzer.analyze`` on the checked-in sample corpora.

Pins per-file metrics and scores, every finding (biomarker type, severity,
lines, impact, details) with its public id, the repo KPIs, and which
refactoring suggestions came out, in the order the engine returns them. Two
corpora: the refactoring archetype trees and the per-language samples. Rewrite
with ``REPOWISE_REWRITE_GOLDEN=1`` only for an intended behaviour change.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from pathlib import Path

import pytest

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


def _analyze(root: Path, *, in_workers: bool = False) -> dict:
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
    analyzer = HealthAnalyzer(
        builder.graph(), git_meta_map={}, parsed_files=parsed, repo_root=root
    )
    report = asyncio.run(analyzer.analyze_async(None)) if in_workers else analyzer.analyze(None)
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


@pytest.mark.parametrize("in_workers", [False, True], ids=["in_process", "in_workers"])
def test_health_engine_matches_golden(tmp_path: Path, monkeypatch, in_workers: bool) -> None:
    # In workers: every walk and every Extract Method file runs in the spawn
    # pool, which must reproduce the in-process golden exactly.
    from repowise.core.analysis.health import engine

    pooled: dict[str, int] = {}
    if in_workers:
        monkeypatch.setattr(engine, "_POOL_MIN_JOBS", 1)
        real_run = engine.run_with_deadline

        def counting_run(fn, items, workers, results, **kwargs):
            real_run(fn, items, workers, results, **kwargs)
            assert not any(isinstance(r, BaseException) for r in results)
            pooled[fn.__name__] = pooled.get(fn.__name__, 0) + len(items)

        monkeypatch.setattr(engine, "run_with_deadline", counting_run)
    actual = {}
    for name, src in CORPORA.items():
        root = tmp_path / name.replace("/", "_")
        _copy_lf(src, root)
        actual[name] = _analyze(root, in_workers=in_workers)
    if os.environ.get("REPOWISE_REWRITE_GOLDEN") == "1" and not in_workers:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name in CORPORA:
        assert actual[name] == expected[name], name
    assert actual.keys() == expected.keys()
    if in_workers:
        assert pooled.get("run_walk_job") and pooled.get("run_extract_method_job")
