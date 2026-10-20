"""Cached parses and walks must never change a comparison.

The cache only skips work whose output is a function of a file's bytes and the
analyzer's versions, so a comparison with it, without it, cold or warm, has to
come out the same; and a version or rules change has to miss rather than serve
an old answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.analysis.change_health import ChangeHealthDeltaService, DeltaRequest
from repowise.core.analysis.change_health import analyzer as analyzer_mod
from repowise.core.analysis.health import engine as engine_mod
from repowise.core.ingestion import parse_cache as parse_cache_mod

from .conftest import Repo, python_complex, python_io_in_loop, typescript_complex


@pytest.fixture
def repo(make_repo) -> Repo:
    repo: Repo = make_repo("cached")
    repo.commit(
        "base",
        {
            "pkg/package.json": '{"name": "pkg"}\n',
            "pkg/app.ts": typescript_complex("run", 2),
            "svc/handler.py": python_io_in_loop(in_loop=False),
            "svc/old.py": python_complex("legacy", 9),
            "svc/gone.py": python_complex("doomed", 7),
        },
    )
    repo.move("svc/old.py", "svc/renamed.py")
    repo.remove("svc/gone.py")
    repo.commit(
        "head",
        {
            "pkg/app.ts": typescript_complex("run", 12),
            "svc/handler.py": python_io_in_loop(in_loop=True),
            "svc/new.py": python_complex("fresh", 14),
        },
    )
    return repo


def _comparable(repo: Repo, cache_dir) -> tuple:
    service = ChangeHealthDeltaService(repo_path=str(repo.path), cache_dir=cache_dir)
    delta = service.compare(DeltaRequest(str(repo.path), "HEAD~1..HEAD"))
    return (
        delta.status,
        delta.explanation,
        [
            (f.change_finding_id, f.change_kind, f.biomarker_type, f.path, f.symbol, f.line_start)
            for f in delta.findings
        ],
        delta.unchanged_total,
        delta.improved_total,
        delta.moved_total,
        delta.resolved_total,
        sorted(delta.skipped.items()),
        repr(delta.files),
        delta.moved_to,
    )


class _Counter:
    def __init__(self, monkeypatch) -> None:
        self.parses = 0
        self.walks = 0
        real_parse, real_walk = analyzer_mod.parse_file, engine_mod.walk_file

        def parse(*args, **kwargs):
            self.parses += 1
            return real_parse(*args, **kwargs)

        def walk(*args, **kwargs):
            self.walks += 1
            return real_walk(*args, **kwargs)

        monkeypatch.setattr(analyzer_mod, "parse_file", parse)
        monkeypatch.setattr(engine_mod, "walk_file", walk)


def test_results_are_identical_with_no_cache_a_cold_one_and_a_warm_one(repo, tmp_path):
    cache = tmp_path / "cache"
    uncached = _comparable(repo, False)
    assert uncached[2], "the fixture must surface findings to compare"
    assert _comparable(repo, cache) == uncached
    assert _comparable(repo, cache) == uncached


def test_a_warm_cache_parses_and_walks_nothing(repo, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    _comparable(repo, cache)
    counter = _Counter(monkeypatch)
    _comparable(repo, cache)
    assert (counter.parses, counter.walks) == (0, 0)


def test_a_new_analyzer_version_walks_again(repo, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    expected = _comparable(repo, cache)
    monkeypatch.setattr(analyzer_mod, "HEALTH_ANALYZER_VERSION", -1)
    counter = _Counter(monkeypatch)
    assert _comparable(repo, cache) == expected
    assert counter.walks > 0


def test_a_new_parser_parses_again(repo, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    _comparable(repo, cache)
    monkeypatch.setattr(parse_cache_mod, "parser_fingerprint", lambda: "another parser")
    counter = _Counter(monkeypatch)
    _comparable(repo, cache)
    assert counter.parses > 0


def test_changed_rules_are_applied_to_a_warm_cache(repo, tmp_path):
    cache = tmp_path / "cache"
    _comparable(repo, cache)
    (repo.path / ".repowise").mkdir(exist_ok=True)
    (repo.path / ".repowise" / "health-rules.json").write_text(
        json.dumps({"disabled_biomarkers": ["complex_method"]}), encoding="utf-8"
    )
    (repo.path / ".repowise" / "config.yaml").write_text(
        "assertions:\n  extra_names: [verify_that]\n", encoding="utf-8"
    )
    assert _comparable(repo, cache) == _comparable(repo, False)
    assert all(f[2] != "complex_method" for f in _comparable(repo, cache)[2])


def test_an_unwritable_cache_location_is_a_cold_cache(repo, tmp_path):
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory", encoding="utf-8")
    assert _comparable(repo, blocked) == _comparable(repo, False)


def test_the_cache_defaults_to_the_index_directory_only_when_there_is_one(repo):
    assert ChangeHealthDeltaService(repo_path=str(repo.path)).cache_dir is None
    (repo.path / ".repowise").mkdir()
    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    assert service.cache_dir == repo.path / ".repowise" / "change_health"
    assert ChangeHealthDeltaService(repo_path=str(repo.path), cache_dir=False).cache_dir is None


def test_the_cache_never_shows_up_as_a_working_tree_change(repo):
    (repo.path / ".repowise").mkdir()
    repo.write("svc/new.py", python_complex("fresher", 16))
    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    first = service.compare(DeltaRequest(str(repo.path), None))
    assert (repo.path / ".repowise" / "change_health").is_dir()
    second = ChangeHealthDeltaService(repo_path=str(repo.path)).compare(
        DeltaRequest(str(repo.path), None)
    )
    assert second.scope.changed == first.scope.changed
    assert not any(Path(p).parts[:2] == (".repowise", "change_health") for p in second.skipped)
