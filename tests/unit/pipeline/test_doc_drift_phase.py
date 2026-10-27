"""The drift pass as a member of the analysis gather.

Pins the wiring rather than the detection rules, which
``tests/unit/doc_drift/`` owns: that the pass runs as part of a real pipeline,
that its report reaches ``PipelineResult``, and that a failure inside it cannot
take the other three analyses down with it.
"""

from __future__ import annotations

from repowise.core.pipeline import run_pipeline
from repowise.core.pipeline.phases.analysis import _run_doc_drift_analysis


def _write_repo(root):
    """A repo whose README tells one truth and one lie."""
    root.mkdir()
    (root / "pkg").mkdir()
    (root / "pkg" / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text(
        "# Demo\n\n"
        "The entry point is `pkg/main.py`.\n"
        "Helpers live in `pkg/helpers.py`.\n",
        encoding="utf-8",
    )
    return root


async def test_pipeline_produces_a_drift_report(tmp_path):
    result = await run_pipeline(_write_repo(tmp_path / "repo"), test_run=True)

    report = result.doc_drift_report
    assert report is not None
    assert report.documents_scanned == 1
    assert [f.target for f in report.findings] == ["pkg/helpers.py"]
    assert report.findings[0].file_path == "README.md"


async def test_markdown_bytes_reach_the_pass_through_source_map(tmp_path):
    """The pass reads bytes ingestion already decoded rather than re-walking.

    Markdown is a registered language spec whose parser returns an empty
    ``ParsedFile`` rather than ``None``, so its bytes land in ``source_map``
    like any other file. If that ever changes, this fails rather than the
    detector silently scanning nothing.
    """
    result = await run_pipeline(_write_repo(tmp_path / "repo"), test_run=True)
    assert "README.md" in result.source_map
    assert result.source_map["README.md"].startswith(b"# Demo")


async def test_a_correct_document_yields_no_findings(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pkg").mkdir()
    (root / "pkg" / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("Entry point: `pkg/main.py`.\n", encoding="utf-8")

    result = await run_pipeline(root, test_run=True)
    assert result.doc_drift_report.total_findings == 0


async def test_a_failure_inside_the_pass_is_swallowed():
    """The gather has no per-member exception wrapper, so each member must be
    total: a raising analysis would otherwise kill dead code, health and
    decisions alongside it.
    """

    class _Exploding(dict):
        def __iter__(self):
            raise RuntimeError("boom")

    # Non-empty, so the analyzer does not swap in a plain ``{}`` for a falsy
    # source map before it ever touches this one.
    exploding = _Exploding({"README.md": b"# hi\n"})
    assert await _run_doc_drift_analysis(exploding, progress=None) is None
