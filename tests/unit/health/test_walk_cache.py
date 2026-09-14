"""The walk cache answers exactly what the walk answers, and never leaks a run.

The complexity walk over a file depends on its bytes, its language and the
walker's version. The pass mutates the result it is handed, so a cached entry
has to come back pristine every time or one run's annotations would become
the next run's input.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.engine import HEALTH_ANALYZER_VERSION
from repowise.core.analysis.health.walk_cache import _CACHE_FILENAME, HealthWalkCache
from repowise.core.ingestion import compute_content_hash

_SRC = b"""
def outer(items):
    total = 0
    for item in items:
        if item:
            total += item
    return total


def inner(x):
    return x + 1
"""


def _walk(tmp_path: Path):
    path = tmp_path / "a.py"
    path.write_bytes(_SRC)
    return walk_file(str(path), "python", _SRC)


def test_a_hit_equals_the_walk_and_is_a_fresh_object(tmp_path: Path) -> None:
    cache = HealthWalkCache(tmp_path, HEALTH_ANALYZER_VERSION)
    key = HealthWalkCache.key("python", compute_content_hash(_SRC))
    walked = _walk(tmp_path)
    cache.put(key, walked)

    first = cache.get(key)
    assert first == walked
    assert first is not walked
    first.functions.clear()
    second = cache.get(key)
    assert second == walked
    assert cache.hits == 2


def test_entries_survive_a_save_and_load(tmp_path: Path) -> None:
    key = HealthWalkCache.key("python", compute_content_hash(_SRC))
    walked = _walk(tmp_path)
    cache = HealthWalkCache(tmp_path, HEALTH_ANALYZER_VERSION)
    cache.put(key, walked)
    cache.save()
    assert (tmp_path / _CACHE_FILENAME).exists()

    again = HealthWalkCache(tmp_path, HEALTH_ANALYZER_VERSION)
    again.load()
    assert again.get(key) == walked
    assert again.get(HealthWalkCache.key("python", "0" * 64)) is None
    assert again.misses == 1


def test_another_analyzer_version_ignores_the_file(tmp_path: Path) -> None:
    key = HealthWalkCache.key("python", compute_content_hash(_SRC))
    cache = HealthWalkCache(tmp_path, HEALTH_ANALYZER_VERSION)
    cache.put(key, _walk(tmp_path))
    cache.save()

    newer = HealthWalkCache(tmp_path, HEALTH_ANALYZER_VERSION + 1)
    newer.load()
    assert newer.get(key) is None


def test_only_used_entries_are_written_back(tmp_path: Path) -> None:
    """A file that left the repository leaves the cache with it."""
    key = HealthWalkCache.key("python", compute_content_hash(_SRC))
    gone = HealthWalkCache.key("python", "f" * 64)
    cache = HealthWalkCache(tmp_path, HEALTH_ANALYZER_VERSION)
    walked = _walk(tmp_path)
    cache.put(key, walked)
    cache.put(gone, walked)
    cache.save()

    run = HealthWalkCache(tmp_path, HEALTH_ANALYZER_VERSION)
    run.load()
    run.get(key)
    run.save()
    third = HealthWalkCache(tmp_path, HEALTH_ANALYZER_VERSION)
    third.load()
    assert third.get(key) is not None
    assert third.get(gone) is None


def test_the_analyzer_walks_once_and_serves_the_second_pass_from_the_cache(tmp_path: Path, monkeypatch) -> None:
    import networkx as nx

    from repowise.core.analysis.health import engine as engine_mod
    from repowise.core.ingestion import ASTParser, FileTraverser

    (tmp_path / "a.py").write_bytes(_SRC)
    (tmp_path / ".repowise").mkdir()
    parser = ASTParser()
    parsed = [parser.parse_file(fi, Path(fi.abs_path).read_bytes()) for fi in FileTraverser(tmp_path).traverse()]
    calls: list[str] = []
    real_walk = engine_mod.walk_file

    def counting_walk(path, language, source):
        calls.append(path)
        return real_walk(path, language, source)

    monkeypatch.setattr(engine_mod, "walk_file", counting_walk)

    def analyzer():
        return engine_mod.HealthAnalyzer(
            nx.DiGraph(),
            git_meta_map={},
            parsed_files=parsed,
            duplication_cache_dir=tmp_path / ".repowise",
            repo_root=tmp_path,
        )

    first = analyzer().analyze(None)
    second = analyzer().analyze(None)
    assert len(calls) == 1
    assert [m.score for m in first.metrics] == [m.score for m in second.metrics]
    assert [(f.biomarker_type, f.function_name) for f in first.findings] == [
        (f.biomarker_type, f.function_name) for f in second.findings
    ]
