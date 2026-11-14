"""An update returns the complete stored drift report after pruning old paths."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from repowise.core.analysis.doc_drift import DocDriftAnalyzer
from repowise.core.analysis.doc_drift.models import DocDriftReport
from repowise.core.analysis.doc_drift.serialize import serialize_finding
from repowise.core.analysis.doc_drift.symbols import SymbolOptions, graph_symbol_names
from repowise.core.workspace.update import UpdateProducts, get_head_commit, update_single_repo_index
from tests.unit.workspace.test_incremental_update import (
    _add_commit,
    _git,
    _make_git_repo,
    _mark_indexed,
    forbid_full_pipeline,  # noqa: F401 (fixture)
)
from tests.unit.workspace.test_update_caller_options import _commit_file

pytestmark = pytest.mark.usefixtures("forbid_full_pipeline")


def _finding_keys(findings):
    return sorted(json.dumps(serialize_finding(f), sort_keys=True) for f in findings)


def _assert_full_parity(repo, products):
    stored = products.doc_drift_report
    assert isinstance(stored, DocDriftReport)
    assert stored.authoritative_paths is None
    assert stored.symbol_scope is None
    full = _full_report(repo, products)
    assert _finding_keys(stored.findings) == _finding_keys(full.findings)
    assert stored.confidence_summary == full.confidence_summary
    assert set(stored.resolved_references) == set(full.resolved_references)
    return stored


def _full_report(repo, products):
    return DocDriftAnalyzer(
        source_map=products.source_map,
        tracked_paths=products.graph_builder.traversed_file_paths,
        repo_root=repo,
        symbols=SymbolOptions(graph_symbol_names(products.graph_builder.graph())),
    ).analyze()


def test_products_keep_a_carried_forward_symbol_finding(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import repowise.core.pipeline.incremental as incremental
    from repowise.cli.commands.update_cmd.incremental import _run_doc_drift_partial

    repo = _make_git_repo(tmp_path)
    _commit_file(repo, ".gitignore", ".repowise/\n")
    _commit_file(repo, "README.md", "Call `alpha()`.\n")
    _commit_file(repo, "GUIDE.md", "See `a.py`.\n")
    _mark_indexed(repo, get_head_commit(repo))
    _add_commit(repo, "b.py")
    assert asyncio.run(update_single_repo_index(repo)).error is None
    _mark_indexed(repo, get_head_commit(repo))

    partials = []
    analyze = incremental.run_doc_drift_partial

    def record(*args, **kwargs):
        report = analyze(*args, **kwargs)
        partials.append(report)
        return report

    monkeypatch.setattr(incremental, "run_doc_drift_partial", record)
    _commit_file(repo, "a.py", "def renamed():\n    return 1\n")
    first: list[UpdateProducts] = []
    assert asyncio.run(update_single_repo_index(repo, products_sink=first)).error is None
    assert first[0].doc_drift_report.total_findings == 1
    base = get_head_commit(repo)
    _mark_indexed(repo, base)

    _commit_file(repo, "b.py", "def unrelated():\n    return 2\n")
    sink: list[UpdateProducts] = []
    assert asyncio.run(update_single_repo_index(repo, products_sink=sink)).error is None
    assert partials[-1].findings == []
    stored = _assert_full_parity(repo, sink[0])
    assert stored.total_findings == 1
    assert [(f.file_path, f.target) for f in stored.findings] == [("README.md", "alpha")]
    assert replace(stored).findings == stored.findings

    cli_report = _run_doc_drift_partial(
        sink[0].graph_builder,
        sink[0].source_map,
        repo_path=repo,
        base_ref=base,
        file_diffs=[SimpleNamespace(path="b.py", old_path=None, status="modified")],
    )
    # Cheap kinds still scan every document; the existing symbol scope stays narrow.
    assert cli_report.documents == frozenset({"README.md", "GUIDE.md"})
    assert cli_report.findings == []


@pytest.mark.parametrize("replacement", [None, "MOVED.md"])
def test_products_prune_deleted_and_renamed_documents(tmp_path, replacement):
    repo = _make_git_repo(tmp_path)
    _commit_file(repo, ".gitignore", ".repowise/\n")
    _commit_file(repo, "README.md", "Call `alpha()` and read `GUIDE.md`.\n")
    _commit_file(repo, "GUIDE.md", "Call `alpha()`.\n")
    _mark_indexed(repo, get_head_commit(repo))
    _commit_file(repo, "a.py", "def renamed():\n    return 1\n")
    first: list[UpdateProducts] = []
    assert asyncio.run(update_single_repo_index(repo, products_sink=first)).error is None
    assert {(f.file_path, f.target) for f in first[0].doc_drift_report.findings} == {
        ("README.md", "alpha"), ("GUIDE.md", "alpha"),
    }
    _mark_indexed(repo, get_head_commit(repo))

    if replacement:
        _git(repo, "mv", "README.md", replacement)
    else:
        _git(repo, "rm", "README.md")
    _git(repo, "commit", "-m", "remove old document path")
    sink: list[UpdateProducts] = []
    assert asyncio.run(update_single_repo_index(repo, products_sink=sink)).error is None
    stored = _assert_full_parity(repo, sink[0])
    assert all(f.file_path != "README.md" for f in stored.findings)
    assert all(r.doc_path != "README.md" for r in stored.resolved_references)


@pytest.mark.parametrize("failed", ["health", "doc_drift"])
def test_one_stored_report_failure_preserves_the_other(tmp_path, monkeypatch, failed):
    import repowise.core.pipeline.resume.rehydrate as rehydrate

    repo = _make_git_repo(tmp_path)
    _mark_indexed(repo, get_head_commit(repo))
    _add_commit(repo, "b.py")

    async def unavailable(*args):
        raise RuntimeError("fixture read failure")

    monkeypatch.setattr(rehydrate, f"rehydrate_{failed}_report", unavailable)
    sink: list[UpdateProducts] = []
    assert asyncio.run(update_single_repo_index(repo, products_sink=sink)).error is None
    assert getattr(sink[0], f"{failed}_report") is None
    other = "doc_drift" if failed == "health" else "health"
    assert getattr(sink[0], f"{other}_report") is not None
