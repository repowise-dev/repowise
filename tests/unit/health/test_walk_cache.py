"""The walk cache answers exactly what the walk answers, and never leaks a run.

The complexity walk over a file depends on its bytes, its grammar and the
walker's code, never the analyzer stamp. The pass mutates the result it is handed, so a cached entry
has to come back pristine every time or one run's annotations would become
the next run's input.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.analysis.health import walk_cache as walk_cache_mod
from repowise.core.analysis.health.asserts.lexicon import AssertVocabulary
from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.engine import HealthAnalyzer
from repowise.core.analysis.health.walk_cache import _CACHE_FILENAME, HealthWalkCache
from repowise.core.ingestion import compute_content_hash
from repowise.core.ingestion.parser import grammar_tag_for
from repowise.core.ingestion.special_handlers import _read_sql_dialect

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
    cache = HealthWalkCache(tmp_path)
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
    cache = HealthWalkCache(tmp_path)
    cache.put(key, walked)
    cache.save()
    assert (tmp_path / _CACHE_FILENAME).exists()

    again = HealthWalkCache(tmp_path)
    again.load()
    assert again.get(key) == walked
    assert again.get(HealthWalkCache.key("python", "0" * 64)) is None
    assert again.misses == 1


def test_changed_walker_code_ignores_the_file(tmp_path: Path, monkeypatch) -> None:
    key = HealthWalkCache.key("python", compute_content_hash(_SRC))
    cache = HealthWalkCache(tmp_path)
    cache.put(key, _walk(tmp_path))
    cache.save()

    monkeypatch.setattr(walk_cache_mod, "walker_fingerprint", lambda: "edited walker")
    newer = HealthWalkCache(tmp_path)
    newer.load()
    assert newer.get(key) is None


def test_only_used_entries_are_written_back(tmp_path: Path) -> None:
    """A file that left the repository leaves the cache with it."""
    key = HealthWalkCache.key("python", compute_content_hash(_SRC))
    gone = HealthWalkCache.key("python", "f" * 64)
    cache = HealthWalkCache(tmp_path)
    walked = _walk(tmp_path)
    cache.put(key, walked)
    cache.put(gone, walked)
    cache.save()

    run = HealthWalkCache(tmp_path)
    run.load()
    run.get(key)
    run.save()
    third = HealthWalkCache(tmp_path)
    third.load()
    assert third.get(key) is not None
    assert third.get(gone) is None


@pytest.mark.parametrize(
    ("parallel", "bump"),
    [(False, False), (True, False), (False, True)],
    ids=["analyze", "analyze_async", "after_analyzer_bump"],
)
def test_the_analyzer_walks_once_and_serves_the_second_pass_from_the_cache(
    tmp_path: Path, monkeypatch, parallel: bool, bump: bool
) -> None:
    # Both paths persist the cache: the parallel one serves ``init`` on large
    # repos, and a walk it never saved is paid again on the next pass. An
    # analyzer stamp bump re-scores but must not re-walk.
    import asyncio

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

    def run():
        return asyncio.run(analyzer().analyze_async(None)) if parallel else analyzer().analyze(None)

    first = run()
    if bump:
        monkeypatch.setattr(engine_mod, "HEALTH_ANALYZER_VERSION", engine_mod.HEALTH_ANALYZER_VERSION + 1)
    second = run()
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
    cache = HealthWalkCache(tmp_path)
    analyzer = SimpleNamespace(
        read_source=lambda _p: src,
        _walk_cache=cache,
        _origins={},
        _origin=lambda _pf, _source=None: "production",
    )

    def parsed(name: str):
        return SimpleNamespace(
            file_info=SimpleNamespace(
                path=name, abs_path=str(tmp_path / name), language="typescript"
            )
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


def test_a_sql_file_keys_on_its_configured_dialect(tmp_path: Path) -> None:
    # The dialect is read from config, not the bytes, so editing it must miss.
    src = b"SELECT * FROM a, b;\n"
    config = tmp_path / ".repowise" / "config.yaml"
    config.parent.mkdir()
    cache = HealthWalkCache(tmp_path)
    analyzer = SimpleNamespace(
        read_source=lambda _p: src,
        _walk_cache=cache,
        _origins={},
        _origin=lambda _pf, _source=None: "production",
    )
    pf = SimpleNamespace(
        file_info=SimpleNamespace(path="q.sql", abs_path=str(tmp_path / "q.sql"), language="sql")
    )
    vocab = AssertVocabulary()

    _read_sql_dialect.cache_clear()
    config.write_text("sql_dialect: postgres\n")
    HealthAnalyzer._walk(analyzer, pf, vocab)
    HealthAnalyzer._walk(analyzer, pf, vocab)
    assert (cache.misses, cache.hits) == (1, 1)

    # The read is memoised per process; a new process sees the edit.
    _read_sql_dialect.cache_clear()
    config.write_text("sql_dialect: mysql\n")
    HealthAnalyzer._walk(analyzer, pf, vocab)
    assert (cache.misses, cache.hits) == (2, 1)


def test_a_walk_without_its_grammar_is_not_cached(tmp_path: Path, monkeypatch) -> None:
    # Empty for want of the grammar, so installing it later must walk the file.
    from repowise.core.ingestion.parser import grammars

    monkeypatch.setitem(grammars._GRAMMAR_FAILURES, "python", "not installed")
    cache = HealthWalkCache(tmp_path)
    key = HealthWalkCache.key("python", compute_content_hash(_SRC))
    cache.put(key, _walk(tmp_path))
    assert cache.get(key) is None
