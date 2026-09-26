"""What the pass retains besides findings: the references that resolved.

The reverse view is built on these rows, so the thing worth pinning is what
never becomes one. A row asserts that a document names a file and the name
still resolves; an ambiguous or uncheckable reference asserts nothing, and
recording one as a fact is how "which documents mention this file" would start
answering with documents that do not.
"""

from __future__ import annotations

from repowise.core.analysis.doc_drift import DocDriftAnalyzer
from repowise.core.analysis.doc_drift.models import DriftKind


def _analyze(files: dict[str, str], config: dict | None = None):
    source_map = {k: v.encode("utf-8") for k, v in files.items()}
    return DocDriftAnalyzer("repo", source_map=source_map).analyze(config)


def _refs(report):
    return {(r.doc_path, r.target_path, r.kind, r.line) for r in report.resolved_references}


def test_a_resolved_path_is_retained():
    report = _analyze(
        {
            "docs/a.md": "## Auth\n\nSee `src/auth.py` for tokens.\n",
            "src/auth.py": "",
        }
    )

    assert report.total_findings == 0
    (ref,) = report.resolved_references
    assert ref.doc_path == "docs/a.md"
    assert ref.target_path == "src/auth.py"
    assert ref.kind is DriftKind.PATH
    assert ref.line == 3
    assert ref.section == "Auth"


def test_a_relative_link_is_stored_as_the_path_it_resolves_to():
    """The join the resolver used to compute and throw away.

    ``docs/guide.md`` written inside ``docs/a.md`` means ``docs/docs/...``
    only if you ignore the document's own directory. Storing what was written
    rather than what it resolved to would file every relative link under a
    path that does not exist.
    """
    report = _analyze(
        {
            "docs/a.md": "See [the guide](guide.md).\n",
            "docs/guide.md": "# Guide\n",
        }
    )

    targets = {r.target_path for r in report.resolved_references}
    assert targets == {"docs/guide.md"}


def test_a_drifted_reference_becomes_a_finding_and_not_a_row():
    """The two are complements. A missing target resolves to nothing at all."""
    report = _analyze(
        {
            "docs/a.md": "See `src/gone.py`.\n",
            "src/present.py": "",
        }
    )

    assert report.total_findings == 1
    assert report.resolved_references == []


def test_an_ambiguous_reference_is_not_recorded_as_a_fact():
    """A basename match is not a resolution, and this store holds only resolutions."""
    report = _analyze(
        {
            "docs/a.md": "See `src/models.py`.\n",
            "src/auth/models.py": "",
        }
    )

    assert report.verdict_summary["ambiguous"] == 1
    assert report.resolved_references == []


def test_an_uncheckable_reference_is_not_recorded():
    """Unanchored targets are files this repository was never supposed to have."""
    report = _analyze({"docs/a.md": "Edit `config.yaml` to begin.\n"})

    assert report.verdict_summary["uncheckable"] == 1
    assert report.resolved_references == []


def test_a_document_naming_itself_is_dropped():
    """A table of contents anchoring its own headings answers no reverse question."""
    report = _analyze({"docs/a.md": "# Setup\n\n[jump](#setup)\n"})

    assert report.verdict_summary["resolved"] == 1
    assert report.resolved_references == []


def test_a_cross_document_anchor_points_at_the_host_document():
    report = _analyze(
        {
            "docs/a.md": "See [setup](guide.md#setup).\n",
            "docs/guide.md": "# Setup\n",
        }
    )

    rows = {(r.target_path, r.kind) for r in report.resolved_references}
    assert ("docs/guide.md", DriftKind.ANCHOR) in rows
    assert all(r.doc_path == "docs/a.md" for r in report.resolved_references)


def test_a_resolved_command_is_not_a_row():
    """A make target is not a file, so it has no place in a path-keyed store."""
    report = _analyze(
        {
            "docs/a.md": "Run `make build` to compile.\n",
            "Makefile": "build:\n\techo hi\n",
        }
    )

    assert report.verdict_summary["resolved"] == 1
    assert report.resolved_references == []


def test_two_references_on_one_line_are_both_retained():
    """The same reason the findings key carries the target: one line, two claims."""
    report = _analyze(
        {
            "docs/a.md": "See [setup](guide.md#setup).\n",
            "docs/guide.md": "# Setup\n",
        }
    )

    assert {r.kind for r in report.resolved_references} == {
        DriftKind.LINK,
        DriftKind.ANCHOR,
    }
    assert len(_refs(report)) == 2


def test_retention_changes_no_finding():
    """The pass's own answer must not move because a second consumer was added."""
    files = {
        "docs/a.md": "See `src/gone.py` and `src/here.py`.\n",
        "src/here.py": "",
    }
    report = _analyze(files)

    assert report.total_findings == 1
    assert report.verdict_summary["resolved"] == 1
    assert [r.target_path for r in report.resolved_references] == ["src/here.py"]
