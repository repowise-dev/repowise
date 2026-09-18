"""The walk cache answers exactly what the walk answers, and never leaks a run.

The complexity walk over a file depends on its bytes, its grammar and the
walker's version. The pass mutates the result it is handed, so a cached entry
has to come back pristine every time or one run's annotations would become
the next run's input.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repowise.core.analysis.health.asserts.lexicon import AssertVocabulary
from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.engine import HEALTH_ANALYZER_VERSION, HealthAnalyzer
from repowise.core.analysis.health.walk_cache import _CACHE_FILENAME, HealthWalkCache
from repowise.core.ingestion import compute_content_hash
from repowise.core.ingestion.parser import grammar_tag_for

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

    def counting_walk(path, language, source, extra_assert_names=frozenset()):
        calls.append(path)
        return real_walk(path, language, source, extra_assert_names)

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


def test_a_tsx_and_a_byte_identical_ts_file_key_apart() -> None:
    # The walk reads .tsx with the JSX grammar and .ts without it, so the same
    # bytes under the two paths are two different walks and must not share a
    # key. Both arrive tagged ``typescript``, which is why the tag alone cannot
    # separate them.
    src = b'it("x", () => { render(1); });\n'
    digest = compute_content_hash(src)
    as_ts = HealthWalkCache.key(grammar_tag_for("typescript", "src/a.ts"), digest)
    as_tsx = HealthWalkCache.key(grammar_tag_for("typescript", "src/a.tsx"), digest)
    assert as_ts != as_tsx
    # Every other language keys exactly as it did before.
    assert HealthWalkCache.key(grammar_tag_for("python", "a.py"), digest) == f"python:{digest}"


def test_the_walk_itself_does_not_serve_a_ts_file_to_a_tsx_file(tmp_path: Path) -> None:
    # The key helper is checked above; this drives the line that uses it, so a
    # revert to keying on the language tag fails here rather than passing.
    src = b'const C = () => <div>{label}</div>;\nit("t", () => { render(<C />); });\n'
    cache = HealthWalkCache(tmp_path, HEALTH_ANALYZER_VERSION)
    analyzer = SimpleNamespace(read_source=lambda _p: src, _walk_cache=cache)

    def parsed(name: str):
        return SimpleNamespace(
            file_info=SimpleNamespace(abs_path=str(tmp_path / name), language="typescript")
        )

    vocab = AssertVocabulary()
    as_ts = HealthAnalyzer._walk(analyzer, parsed("a.ts"), vocab)
    as_tsx = HealthAnalyzer._walk(analyzer, parsed("a.tsx"), vocab)

    # Two misses: the second file was walked, not served the first one's entry.
    assert cache.misses == 2
    assert cache.hits == 0
    # And the two walks really do differ, or the assertion above proves nothing.
    ts_asserts = sum(f.assertion_count for f in as_ts.functions)
    tsx_asserts = sum(f.assertion_count for f in as_tsx.functions)
    assert (len(as_ts.functions), ts_asserts) != (len(as_tsx.functions), tsx_asserts)
