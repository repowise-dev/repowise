"""Parse-cache equivalence: cached ingest must match a fresh parse exactly.

The cache snapshots ParsedFiles at parse time keyed by (path, content hash)
under a parser fingerprint. Every scenario asserts the cached run is
ParsedFile-equal AND graph-isomorphic to a from-scratch build — after file
edit, add, delete, rename, and an import-changing edit. Corrupt or
stale-fingerprint caches must degrade silently to a full parse.
"""

from __future__ import annotations

import pickle

import networkx as nx
import pytest

from repowise.cli.commands.update_cmd import _build_repo_graph
from repowise.core.ingestion import compute_content_hash
from repowise.core.ingestion.parse_cache import ParseCache, parser_fingerprint


def _write_fixture(repo) -> None:
    (repo / "util.py").write_text(
        "def helper(x):\n    return x + 1\n",
        encoding="utf-8",
    )
    (repo / "main.py").write_text(
        "from util import helper\n\n\ndef main():\n    return helper(2)\n",
        encoding="utf-8",
    )
    (repo / "other.py").write_text(
        "VALUE = 3\n",
        encoding="utf-8",
    )


def _cache_path(repo) -> object:
    return repo / ".repowise" / "parse_cache.pkl"


def _build(repo):
    """Run the update path's full ingest; returns (parsed_files, graph)."""
    parsed, _src, gb, _structure, _count = _build_repo_graph(repo, [], collect_sources=False)
    return sorted(parsed, key=lambda p: p.file_info.path), gb.graph()


def _fresh_build(repo):
    """Ground truth: same build with the cache file removed."""
    cache_file = _cache_path(repo)
    if cache_file.exists():
        cache_file.unlink()
    return _build(repo)


def _assert_equivalent(repo) -> None:
    """Cached run == from-scratch run: ParsedFile-equal and graph-isomorphic."""
    cached_parsed, cached_graph = _build(repo)  # warm cache from prior run
    fresh_parsed, fresh_graph = _fresh_build(repo)

    assert [p.file_info.path for p in cached_parsed] == [p.file_info.path for p in fresh_parsed]
    for cp, fp in zip(cached_parsed, fresh_parsed, strict=True):
        assert cp == fp, cp.file_info.path

    assert nx.utils.graphs_equal(cached_graph, fresh_graph)


@pytest.fixture()
def repo(tmp_path):
    _write_fixture(tmp_path)
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def test_warm_cache_equivalent_unchanged(repo):
    _build(repo)  # cold run populates the cache
    _assert_equivalent(repo)


def test_equivalent_after_edit(repo):
    _build(repo)
    (repo / "util.py").write_text(
        "def helper(x):\n    return x * 2\n\n\ndef extra(y):\n    return y\n",
        encoding="utf-8",
    )
    _assert_equivalent(repo)


def test_equivalent_after_add(repo):
    _build(repo)
    (repo / "added.py").write_text("def added_fn():\n    return 9\n", encoding="utf-8")
    _assert_equivalent(repo)


def test_equivalent_after_delete(repo):
    _build(repo)
    (repo / "other.py").unlink()
    _assert_equivalent(repo)


def test_equivalent_after_rename(repo):
    _build(repo)
    (repo / "other.py").rename(repo / "renamed.py")
    _assert_equivalent(repo)
    # Symbol IDs embed the path — the renamed file must carry the new path.
    parsed, _ = _build(repo)
    by_path = {p.file_info.path: p for p in parsed}
    assert "renamed.py" in by_path
    assert "other.py" not in by_path


def test_equivalent_after_import_changing_edit(repo):
    _build(repo)
    (repo / "main.py").write_text(
        "from other import VALUE\n\n\ndef main():\n    return VALUE\n",
        encoding="utf-8",
    )
    _assert_equivalent(repo)


def test_corrupt_cache_falls_back_to_full_parse(repo):
    _build(repo)
    _cache_path(repo).write_bytes(b"\x00not a pickle")
    cached_parsed, cached_graph = _build(repo)
    fresh_parsed, fresh_graph = _fresh_build(repo)
    for cp, fp in zip(cached_parsed, fresh_parsed, strict=True):
        assert cp == fp
    assert nx.utils.graphs_equal(cached_graph, fresh_graph)


def test_fingerprint_mismatch_invalidates(repo, tmp_path):
    _build(repo)
    cache_file = _cache_path(repo)
    payload = pickle.loads(cache_file.read_bytes())
    assert payload["fingerprint"] == parser_fingerprint()
    payload["fingerprint"] = "stale-fingerprint"
    cache_file.write_bytes(pickle.dumps(payload))

    cache = ParseCache(repo / ".repowise")
    cache.load()
    fi = _make_file_info(repo / "other.py", "other.py")
    source = (repo / "other.py").read_bytes()
    assert cache.get(fi, compute_content_hash(source)) is None
    assert cache.misses == 1


def test_dataclass_schema_change_invalidates_cache(repo, monkeypatch):
    """A ParsedFile (or nested dataclass) field change must self-invalidate.

    Regression: ``parse_cache.pkl`` pickles ParsedFile object graphs, so a new
    field on a cached dataclass made an old entry deserialize without the
    attribute, crashing the graph builder. The fingerprint now folds in every
    cached dataclass's field set, so the stale entry is treated as a miss.
    """
    import dataclasses

    from repowise.core.ingestion import models, parse_cache

    _build(repo)  # populate the cache under the current fingerprint
    cache_file = _cache_path(repo)
    assert pickle.loads(cache_file.read_bytes())["fingerprint"] == parser_fingerprint()

    # Simulate a release that adds a field to ParsedFile.
    @dataclasses.dataclass
    class _Evolved(models.ParsedFile):
        new_field: int = 0

    _Evolved.__module__ = models.__name__  # masquerade as a models.py type
    monkeypatch.setattr(models, "ParsedFile", _Evolved)
    parse_cache.parser_fingerprint.cache_clear()
    try:
        assert parser_fingerprint() != pickle.loads(cache_file.read_bytes())["fingerprint"]

        cache = ParseCache(repo / ".repowise")
        cache.load()
        fi = _make_file_info(repo / "other.py", "other.py")
        source = (repo / "other.py").read_bytes()
        assert cache.get(fi, compute_content_hash(source)) is None
    finally:
        parse_cache.parser_fingerprint.cache_clear()


def _make_file_info(abs_path, rel_path):
    from datetime import UTC, datetime

    from repowise.core.ingestion.models import FileInfo

    return FileInfo(
        path=rel_path,
        abs_path=str(abs_path),
        language="python",
        size_bytes=abs_path.stat().st_size,
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def test_hit_rebinds_fresh_file_info_and_isolates_mutations(repo):
    from repowise.core.ingestion import ASTParser

    fi = _make_file_info(repo / "main.py", "main.py")
    source = (repo / "main.py").read_bytes()
    chash = compute_content_hash(source)
    parsed = ASTParser().parse_file(fi, source)

    cache = ParseCache(repo / ".repowise")
    cache.put(parsed, chash)

    fresh_fi = _make_file_info(repo / "main.py", "main.py")
    fresh_fi.git_hash = "abc123"
    hit1 = cache.get(fresh_fi, chash)
    hit2 = cache.get(fresh_fi, chash)

    # Fresh FileInfo rebound on the hit.
    assert hit1.file_info is fresh_fi
    assert hit1.file_info.git_hash == "abc123"
    # Distinct object graphs: graph-build mutations cannot leak between runs.
    assert hit1 is not hit2 and hit1 is not parsed
    assert hit1.imports and hit1.imports[0].resolved_file is None
    hit1.imports[0].resolved_file = "mutated.py"
    assert hit2.imports[0].resolved_file is None


def test_deleted_files_age_out_of_cache(repo):
    _build(repo)
    (repo / "other.py").unlink()
    _build(repo)  # rewrite keeps only entries touched this run
    payload = pickle.loads(_cache_path(repo).read_bytes())
    cached_paths = {path for path, _hash in payload["files"]}
    assert "other.py" not in cached_paths
    assert "main.py" in cached_paths
