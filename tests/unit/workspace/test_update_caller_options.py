"""The opt-in caller options on ``update_single_repo_index``.

A products sink, a switch that reports "full index needed" instead of running
one, and a whole-repo health re-score. Defaults leave the update unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

from repowise.core.workspace.update import (
    UpdateProducts,
    get_head_commit,
    update_single_repo_index,
)
from tests.unit.workspace.test_incremental_update import (
    _add_commit,
    _git,
    _make_git_repo,
    _mark_indexed,
    forbid_full_pipeline,  # noqa: F401 (fixture)
    stub_full_pipeline,  # noqa: F401 (fixture)
)


@pytest.fixture
def forbid_index_repo_full(monkeypatch):
    import repowise.core.pipeline.full_index as full_index_mod

    async def _boom(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("index_repo_full must not run without the fallback")

    monkeypatch.setattr(full_index_mod, "index_repo_full", _boom)


def _indexed_repo_with_new_commit(tmp_path, **state):
    repo = _make_git_repo(tmp_path)
    _mark_indexed(repo, get_head_commit(repo), **state)
    _add_commit(repo, "b.py")
    return repo


def _stored_metric_paths(repo) -> set[str]:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_health_metrics
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import Repository

    async def _read() -> set[str]:
        from sqlalchemy import select

        engine = create_engine(resolve_db_url(repo))
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo_id = (await session.execute(select(Repository.id))).scalar_one()
                return {m.file_path for m in await get_health_metrics(session, repo_id)}
        finally:
            await engine.dispose()

    return asyncio.run(_read())


def test_sink_receives_the_update_products(tmp_path, forbid_full_pipeline):  # noqa: F811
    repo = _indexed_repo_with_new_commit(tmp_path)
    sink: list[UpdateProducts] = []

    result = asyncio.run(update_single_repo_index(repo, products_sink=sink))

    assert result.updated is True and result.error is None
    assert len(sink) == 1
    products = sink[0]
    paths = {pf.file_info.path for pf in products.parsed_files}
    # The whole repo, not only the changed file.
    assert {"a.py", "b.py"} <= paths
    assert {"a.py", "b.py"} <= set(products.source_map)
    assert products.file_count == result.file_count
    assert "a.py" in products.graph_builder.graph()
    assert products.repo_structure is not None
    assert products.dead_code_report is not None
    assert products.doc_drift_report is not None
    assert products.health_report is not None


def test_sink_stays_empty_when_nothing_changed(tmp_path, forbid_full_pipeline):  # noqa: F811
    repo = _make_git_repo(tmp_path)
    _mark_indexed(repo, get_head_commit(repo))
    _git(repo, "commit", "--allow-empty", "-m", "empty")
    sink: list[UpdateProducts] = []

    result = asyncio.run(update_single_repo_index(repo, products_sink=sink))

    assert result.updated is True
    assert sink == []


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("never_indexed", "not_indexed"),
        ("config_drift", "config_changed"),
        ("base_missing", "base_missing"),
        ("update_failed", "update_failed"),
    ],
)
def test_no_fallback_reports_full_index_needed(
    tmp_path, monkeypatch, forbid_index_repo_full, setup, reason
):
    repo = _make_git_repo(tmp_path)
    if setup == "config_drift":
        _mark_indexed(repo, get_head_commit(repo), config_fingerprint="0" * 64)
    elif setup == "base_missing":
        _mark_indexed(repo, "deadbeef" * 5)
    elif setup == "update_failed":
        import repowise.core.pipeline.incremental as incremental_mod

        async def _boom(*args, **kwargs):
            raise RuntimeError("incremental exploded")

        _mark_indexed(repo, get_head_commit(repo))
        monkeypatch.setattr(incremental_mod, "rebuild_graph_and_git", _boom)
    _add_commit(repo, "b.py")
    sink: list[UpdateProducts] = []

    result = asyncio.run(
        update_single_repo_index(repo, full_index_fallback=False, products_sink=sink)
    )

    assert result.updated is False
    assert result.skipped_reason == "full_index_required"
    assert result.full_index_reason == reason
    if setup == "update_failed":
        assert result.error == "RuntimeError: incremental exploded"
    else:
        assert result.error is None
    assert sink == []


def test_rescore_covers_unchanged_files(tmp_path, forbid_full_pipeline):  # noqa: F811
    repo = _indexed_repo_with_new_commit(tmp_path)
    sink: list[UpdateProducts] = []

    result = asyncio.run(update_single_repo_index(repo, rescore_health=True, products_sink=sink))

    assert result.health_rescored is True
    # a.py did not change; only the whole-repo re-score writes its row.
    assert {"a.py", "b.py"} <= _stored_metric_paths(repo)
    assert {"a.py", "b.py"} <= {m.file_path for m in sink[0].health_report.metrics}


def test_without_rescore_unchanged_files_keep_their_rows(
    tmp_path,
    forbid_full_pipeline,  # noqa: F811
    monkeypatch,
):
    import repowise.core.pipeline.incremental.rescore as rescore_mod

    async def _boom(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("no re-score by default")

    monkeypatch.setattr(rescore_mod, "rescore_health_from_store", _boom)
    repo = _indexed_repo_with_new_commit(tmp_path)

    result = asyncio.run(update_single_repo_index(repo))

    assert result.health_rescored is False
    stored = _stored_metric_paths(repo)
    assert "b.py" in stored and "a.py" not in stored


def test_defaults_keep_the_full_index_fallback(tmp_path, stub_full_pipeline):  # noqa: F811
    repo = _make_git_repo(tmp_path)

    result = asyncio.run(update_single_repo_index(repo))

    assert len(stub_full_pipeline) == 1
    assert result.updated is True
    assert result.skipped_reason is None
    assert result.full_index_reason is None
    assert result.health_rescored is False


def test_update_drops_the_graph_seed_before_rewriting_the_graph(
    tmp_path,
    monkeypatch,
    forbid_full_pipeline,  # noqa: F811
):
    import repowise.core.pipeline.incremental.graph_seed as graph_seed

    async def _skipped_save(_repo_path, _builder):
        return None

    # A save that writes nothing must not leave the old snapshot behind.
    monkeypatch.setattr(graph_seed, "save_seed_edge_cache", _skipped_save)
    repo = _indexed_repo_with_new_commit(tmp_path)
    seed = repo / ".repowise" / "graph_seed_edges.pkl"
    seed.write_bytes(b"snapshot of the graph before this update")

    result = asyncio.run(update_single_repo_index(repo))

    assert result.updated is True and result.error is None
    assert not seed.exists()


_BRANCHY = "def tangled(a, b, c, d):\n" + "".join(
    f"    if a > {i}:\n        if b > {i}:\n            if c > {i}:\n"
    f"                if d > {i}:\n                    a += {i}\n"
    for i in range(14)
) + "    return a\n"


def _commit_file(repo, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"edit {name}")


def _forbid_rescore(monkeypatch) -> None:
    import repowise.core.pipeline.incremental.rescore as rescore_mod

    async def _boom(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("no re-score without rescore_health")

    monkeypatch.setattr(rescore_mod, "rescore_health_from_store", _boom)


def test_sink_report_covers_the_repo_without_a_rescore(
    tmp_path,
    forbid_full_pipeline,  # noqa: F811
    monkeypatch,
):
    from repowise.core.pipeline.incremental.rescore import rescore_health_from_store

    repo = _make_git_repo(tmp_path)
    _commit_file(repo, "a.py", _BRANCHY)
    _mark_indexed(repo, get_head_commit(repo))
    _add_commit(repo, "b.py")
    # Every file scored once, as an index leaves the store.
    asyncio.run(update_single_repo_index(repo, rescore_health=True))
    _commit_file(repo, "c.py", "def gamma():\n    return 3\n")
    _forbid_rescore(monkeypatch)
    sink: list[UpdateProducts] = []

    result = asyncio.run(update_single_repo_index(repo, products_sink=sink))

    assert result.health_rescored is False
    report = sink[0].health_report
    # a.py did not change, yet the report carries it: it comes from the store.
    assert {m.file_path for m in report.metrics} == _stored_metric_paths(repo)
    assert {"a.py", "b.py", "c.py"} <= {m.file_path for m in report.metrics}
    assert any(f.file_path == "a.py" for f in report.findings)
    assert report.kpis.get("average_health") is not None

    # The same scores and findings a full re-score of that store produces.
    monkeypatch.undo()
    rescored = asyncio.run(
        rescore_health_from_store(
            repo, sink[0].graph_builder, sink[0].parsed_files, [], log=lambda *_: None
        )
    )
    assert {m.file_path: m.score for m in report.metrics} == {
        m.file_path: m.score for m in rescored.metrics
    }

    def keys(findings):
        return sorted((f.file_path, f.biomarker_type, f.function_name) for f in findings)

    assert keys(report.findings) == keys(rescored.findings)
    assert {(f.severity, repr(f.details)) for f in report.findings} == {
        (f.severity, repr(f.details)) for f in rescored.findings
    }
    assert report.kpis["average_health"] == rescored.kpis["average_health"]

    def plans(suggestions):
        # Performance plans are stored by their own finalizer, not the report.
        return sorted(
            (s.file_path, s.refactoring_type, s.target_symbol)
            for s in suggestions
            if s.refactoring_type != "performance_fix"
        )

    assert plans(report.refactoring_suggestions) == plans(rescored.refactoring_suggestions)


def test_rescore_skips_the_changed_files_health_pass(
    tmp_path,
    forbid_full_pipeline,  # noqa: F811
    monkeypatch,
):
    import repowise.core.pipeline.incremental as incremental_mod
    import repowise.core.pipeline.incremental.health as health_mod

    health_passes: list[bool] = []
    real_partial = incremental_mod.run_partial_analysis

    def _recording(*args, **kwargs):
        health_passes.append(kwargs.get("health", True))
        return real_partial(*args, **kwargs)

    async def _no_refresh(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("a re-score replaces the history refresh")

    monkeypatch.setattr(incremental_mod, "run_partial_analysis", _recording)
    monkeypatch.setattr(health_mod, "refresh_unchanged_history", _no_refresh)
    repo = _indexed_repo_with_new_commit(tmp_path)

    result = asyncio.run(update_single_repo_index(repo, rescore_health=True))

    assert result.health_rescored is True
    # The changed-files pass is skipped; the re-score scores every file.
    assert health_passes == [False]
    assert {"a.py", "b.py"} <= _stored_metric_paths(repo)
    assert _snapshot_count(repo) == 1


def _snapshot_count(repo) -> int:
    from sqlalchemy import func, select

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import HealthSnapshot

    async def _read() -> int:
        engine = create_engine(resolve_db_url(repo))
        try:
            async with get_session(create_session_factory(engine)) as session:
                return (await session.execute(select(func.count(HealthSnapshot.id)))).scalar_one()
        finally:
            await engine.dispose()

    return asyncio.run(_read())


def test_update_reports_phase_timings(tmp_path, forbid_full_pipeline):  # noqa: F811
    repo = _indexed_repo_with_new_commit(tmp_path)

    result = asyncio.run(update_single_repo_index(repo, rescore_health=True))

    timings = result.phase_timings
    assert timings is not None
    for phase in (
        "run",
        "diff",
        "rebuild",
        "analysis.stored_reads",
        "analysis.dead_code",
        "analysis.doc_drift",
        "knowledge_graph",
        "persist",
        "rescore",
    ):
        assert phase in timings, phase
    assert any(name.startswith("rescore.") for name in timings)
    assert all(seconds >= 0 for seconds in timings.values())
    assert timings["run"] >= timings["persist"]


@pytest.mark.usefixtures("forbid_full_pipeline")
def test_sink_drift_report_keeps_a_carried_forward_symbol_finding(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import repowise.core.pipeline.incremental as incremental
    from repowise.cli.commands.update_cmd.incremental import _run_doc_drift_partial
    from repowise.core.analysis.doc_drift import DocDriftAnalyzer
    from repowise.core.analysis.doc_drift.serialize import serialize_finding
    from repowise.core.analysis.doc_drift.symbols import SymbolOptions, graph_symbol_names

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
    result = asyncio.run(update_single_repo_index(repo, products_sink=first))
    assert result.error is None
    assert first[0].doc_drift_report.total_findings == 1
    # README has no stored reverse edge for alpha; the changed name includes it.
    assert "README.md" in partials[-1].documents
    base = get_head_commit(repo)
    _mark_indexed(repo, base)

    _commit_file(repo, "b.py", "def unrelated():\n    return 2\n")
    sink: list[UpdateProducts] = []
    result = asyncio.run(update_single_repo_index(repo, products_sink=sink))
    assert result.error is None
    assert partials[-1].findings == []
    products = sink[0]
    stored = products.doc_drift_report
    assert stored.total_findings == 1
    assert [(f.file_path, f.target) for f in stored.findings] == [("README.md", "alpha")]
    full = DocDriftAnalyzer(
        source_map=products.source_map,
        tracked_paths=products.graph_builder.traversed_file_paths,
        repo_root=repo,
        symbols=SymbolOptions(graph_symbol_names(products.graph_builder.graph())),
    ).analyze()
    assert [serialize_finding(f) for f in stored.findings] == [
        serialize_finding(f) for f in full.findings
    ]
    assert stored.confidence_summary == full.confidence_summary

    cli_report = _run_doc_drift_partial(
        products.graph_builder, products.source_map, repo_path=repo, base_ref=base,
        file_diffs=[SimpleNamespace(path="b.py", old_path=None, status="modified")],
    )
    assert cli_report.documents == frozenset()
    assert cli_report.findings == []


@pytest.mark.usefixtures("forbid_full_pipeline")
@pytest.mark.parametrize("replacement", [None, "MOVED.md"])
def test_sink_drift_report_prunes_deleted_and_renamed_documents(tmp_path, replacement):
    from repowise.core.analysis.doc_drift import DocDriftAnalyzer
    from repowise.core.analysis.doc_drift.symbols import SymbolOptions, graph_symbol_names
    from tests.unit.pipeline.test_doc_drift_incremental import _finding_keys

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
    assert any(r.doc_path == "README.md" for r in first[0].doc_drift_report.resolved_references)
    _mark_indexed(repo, get_head_commit(repo))

    if replacement:
        _git(repo, "mv", "README.md", replacement)
    else:
        _git(repo, "rm", "README.md")
    _git(repo, "commit", "-m", "remove old document path")
    sink: list[UpdateProducts] = []
    assert asyncio.run(update_single_repo_index(repo, products_sink=sink)).error is None
    products = sink[0]
    stored = products.doc_drift_report
    assert all(f.file_path != "README.md" for f in stored.findings)
    assert all(r.doc_path != "README.md" for r in stored.resolved_references)
    full = DocDriftAnalyzer(
        source_map=products.source_map,
        tracked_paths=products.graph_builder.traversed_file_paths,
        repo_root=repo,
        symbols=SymbolOptions(graph_symbol_names(products.graph_builder.graph())),
    ).analyze()
    assert _finding_keys(stored.findings) == _finding_keys(full.findings)
    assert set(stored.resolved_references) == set(full.resolved_references)
