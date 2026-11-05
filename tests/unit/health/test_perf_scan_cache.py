"""Persist only successful local scans, with the same reports on a warm update."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import pytest

from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.analysis.health.perf import orm_models, scan_cache, unbounded_reduction
from repowise.core.analysis.health.perf.scan_cache import PerfScanCache, scan_cached
from repowise.core.ingestion import ASTParser, FileTraverser, compute_content_hash


@pytest.mark.parametrize("invalidated", ["scanner", "corrupt", "grammar", "parse"])
def test_invalid_or_failed_model_scan_is_recomputed(tmp_path, monkeypatch, invalidated):
    source = b'class Owner:\n    child = relationship("Child")\n'
    pf = SimpleNamespace(
        file_info=SimpleNamespace(abs_path=str(tmp_path / "a.py"), language="python"),
        content_hash=compute_content_hash(source),
    )
    cache = PerfScanCache(tmp_path, "orm_models")
    with monkeypatch.context() as patch:
        if invalidated == "grammar":
            patch.setattr(scan_cache, "grammar_failed", lambda _tag: True)
        elif invalidated == "parse":
            patch.setattr(orm_models, "_scan_file", lambda *args: None)
        orm_models.build_model_index([pf], lambda _p: source, cache=cache)
        cache.save()

    if invalidated == "scanner":
        monkeypatch.setattr(scan_cache, "scan_fingerprint", lambda: "changed scanner")
    elif invalidated == "corrupt":
        (tmp_path / "health_orm_models_cache.pkl").write_bytes(b"broken cache")
    again = PerfScanCache(tmp_path, "orm_models")
    again.load()
    reads = []

    def read(path):
        reads.append(path)
        return source

    result = orm_models.build_model_index([pf], read, cache=again)
    assert "child" in result.relations["Owner"]
    assert len(reads) == 1


def test_files_without_ingestion_hashes_key_on_actual_bytes_and_grammar(tmp_path):
    info = SimpleNamespace(abs_path="a.ts", language="typescript")
    pf = SimpleNamespace(file_info=info)
    cache = PerfScanCache(tmp_path, "test")
    scans = []

    def scan(source):
        scans.append(source)
        return [source]

    assert scan_cached(pf, lambda _p: b"one", scan, cache) == [b"one"]
    assert scan_cached(pf, lambda _p: b"one", scan, cache) == [b"one"]
    assert scans == [b"one"]
    assert scan_cached(pf, lambda _p: b"two", scan, cache) == [b"two"]
    info.abs_path = "a.tsx"
    assert scan_cached(pf, lambda _p: b"two", scan, cache) == [b"two"]
    assert scans == [b"one", b"two", b"two"]


@pytest.mark.parametrize("parallel", [False, True], ids=["sync", "async"])
def test_analyzer_reuses_both_scans_and_preserves_partial_report(tmp_path, monkeypatch, parallel):
    import asyncio

    (tmp_path / "models.py").write_text('''\
class Build:
    owner = relationship("Owner")
''')
    (tmp_path / "query.py").write_text('''\
def latest(session):
    rows = session.execute(select(Build)).scalars().all()
    seen = set()
    for row in rows:
        if row.owner_id in seen:
            continue
        seen.add(row.owner_id)
''')
    parser = ASTParser()
    parsed = [
        parser.parse_file(info, Path(info.abs_path).read_bytes())
        for info in FileTraverser(tmp_path).traverse()
    ]
    scans = []
    for module, name in [(orm_models, "_scan_file"), (unbounded_reduction, "_scan_hits")]:
        original = getattr(module, name)

        def count(*args, original=original, **kwargs):
            scans.append(original.__name__)
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, count)

    def run(changed_files=None, *, cached=True):
        analyzer = HealthAnalyzer(
            nx.DiGraph(), parsed_files=parsed,
            duplication_cache_dir=tmp_path / ".repowise" if cached else None,
            repo_root=tmp_path,
        )
        if parallel:
            return asyncio.run(analyzer.analyze_async(None, changed_files=changed_files))
        return analyzer.analyze(None, changed_files=changed_files)

    full = run()
    assert "_scan_file" in scans and "_scan_hits" in scans
    assert any(f.biomarker_type == "unbounded_read_reduced_in_memory" for f in full.findings)
    scans.clear()
    warm = run()
    assert scans == []
    assert warm.findings == full.findings
    assert warm.performance_authoritative_paths == full.performance_authoritative_paths
    partial = run({"query.py"})
    assert scans == []
    assert partial.findings == [f for f in full.findings if f.file_path == "query.py"]
    uncached = run({"query.py"}, cached=False)
    assert partial.findings == uncached.findings
    assert partial.performance_authoritative_paths == uncached.performance_authoritative_paths
    scans.clear()
    # A partial pass must retain scans for the files it skipped.
    assert run().findings == full.findings
    assert scans == []
