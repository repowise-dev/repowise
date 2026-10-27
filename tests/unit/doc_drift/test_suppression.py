"""Inline ``repowise-drift-ignore`` markers: scope, prose-only, and counted."""

from __future__ import annotations

from repowise.core.analysis.doc_drift import DocDriftAnalyzer
from repowise.core.analysis.doc_drift.extractor import extract, extract_with_suppressed

LINE = "<!-- repowise-drift-ignore -->"
FILE = "<!-- repowise-drift-ignore-file -->"


def _targets(refs) -> list[str]:
    return [r.target for r in refs]


def test_line_marker_covers_its_own_line_and_the_next():
    text = (
        f"`src/a.py` {LINE}\n"
        "`src/b.py`\n"
        "`src/c.py`\n"
    )
    refs, silenced = extract_with_suppressed(text, "docs/x.md")
    assert _targets(silenced) == ["src/a.py", "src/b.py"]
    assert _targets(refs) == ["src/c.py"]


def test_file_marker_anywhere_silences_the_whole_document():
    text = "`src/a.py`\n\n`src/b.py`\n" + FILE + "\n"
    refs, silenced = extract_with_suppressed(text, "docs/x.md")
    assert refs == []
    assert _targets(silenced) == ["src/a.py", "src/b.py"]


def test_marker_inside_a_fence_does_nothing():
    text = f"```markdown\n{LINE}\n{FILE}\n```\n`src/a.py`\n"
    refs, silenced = extract_with_suppressed(text, "docs/x.md")
    assert _targets(refs) == ["src/a.py"]
    assert silenced == []


def test_marker_quoted_in_a_code_span_does_nothing():
    """A document explaining the marker must not silence itself."""
    text = f"Put `{FILE}` in a document.\n`src/a.py`\n"
    refs, silenced = extract_with_suppressed(text, "docs/x.md")
    assert _targets(refs) == ["src/a.py"]
    assert silenced == []


def test_extract_drops_suppressed_references():
    text = f"{LINE}\n`src/a.py`\n`src/b.py`\n"
    assert _targets(extract(text, "docs/x.md")) == ["src/b.py"]


def _analyze(files: dict[str, str], config: dict | None = None):
    source_map = {k: v.encode("utf-8") for k, v in files.items()}
    return DocDriftAnalyzer("repo", source_map=source_map).analyze(config)


def test_report_counts_suppressed_references_instead_of_dropping_them():
    report = _analyze(
        {
            "docs/a.md": f"{LINE}\nSee `src/gone.py`.\nSee `src/lost.py`.\n",
            "src/app.py": "",
        }
    )
    assert report.suppressed == 1
    assert [f.target for f in report.findings] == ["src/lost.py"]


def test_suppressed_count_respects_disabled_kinds():
    report = _analyze(
        {"docs/a.md": f"{FILE}\n`src/gone.py` and `make nope`\n", "Makefile": "all:\n"},
        {"check_command": False},
    )
    assert report.suppressed == 1
    assert report.total_findings == 0
