"""Full and resume ingestion share their stages and return files in walk order.

Insertion order breaks ties in reference resolution, so the order files reach
GraphBuilder is part of the graph's identity, and resumed and fresh runs must
hand analysis the same order. FileInfo builds are slowed so they finish in
reverse walk order: a completion-ordered collection fails here.
"""

from __future__ import annotations

import time

import pytest

from repowise.core.ingestion import FileTraverser
from repowise.core.pipeline import parse_pool
from repowise.core.pipeline.phases import ingestion as ing

_FILES = 6

KW = {"exclude_patterns": None, "skip_tests": False, "skip_infra": False, "progress": None}


@pytest.fixture
def repo(tmp_path):
    for i in range(_FILES):
        (tmp_path / f"mod_{i}.py").write_text(f"def f_{i}(x):\n    return x + {i}\n")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "inner.py").write_text("from mod_0 import f_0\n\n\ndef g():\n    return f_0(1)\n")
    return tmp_path


@pytest.fixture
def walk_order(monkeypatch, repo):
    """Walk order as relative paths; earlier paths finish their build last."""
    walk = list(FileTraverser(repo)._walk())
    rank = {p: i for i, p in enumerate(walk)}
    real = FileTraverser._build_file_info

    def slow_first(self, path):
        time.sleep((len(walk) - rank.get(path, 0)) * 0.05)
        return real(self, path)

    monkeypatch.setattr(FileTraverser, "_build_file_info", slow_first)
    return [p.relative_to(repo).as_posix() for p in walk]


@pytest.fixture
def single_worker_pool(monkeypatch):
    real = parse_pool.ProcessPoolExecutor

    def one(*args, **kwargs):
        kwargs["max_workers"] = 1
        return real(*args, **kwargs)

    monkeypatch.setattr(parse_pool, "ProcessPoolExecutor", one)


async def test_full_ingestion_keeps_walk_order(repo, walk_order, single_worker_pool):
    parsed, file_infos, _s, source_map, gb, _stats, _tech = await ing._run_ingestion(repo, **KW)

    expected = [p for p in walk_order if p in source_map]
    assert len(expected) == _FILES + 1
    assert [fi.path for fi in file_infos if fi.path in source_map] == expected
    assert [p.file_info.path for p in parsed] == expected
    assert list(source_map) == expected
    assert list(gb._parsed_files) == expected


async def test_resume_reparse_keeps_walk_order(repo, walk_order, single_worker_pool):
    parsed, file_infos, _s, source_map, _tech = await ing.reparse_for_resume(repo, **KW)

    expected = [p for p in walk_order if p in source_map]
    assert len(expected) == _FILES + 1
    assert [fi.path for fi in file_infos if fi.path in source_map] == expected
    assert [p.file_info.path for p in parsed] == expected
    assert list(source_map) == expected


async def test_both_entry_points_use_the_shared_stages(repo, monkeypatch, single_worker_pool):
    calls: list[tuple[str, str]] = []
    real_traverse, real_parse = ing._traverse_stage, ing._parse_stage
    current = ""

    async def traverse(*args, **kwargs):
        calls.append(("traverse", current))
        return await real_traverse(*args, **kwargs)

    async def parse(*args, **kwargs):
        calls.append(("parse", current))
        return await real_parse(*args, **kwargs)

    monkeypatch.setattr(ing, "_traverse_stage", traverse)
    monkeypatch.setattr(ing, "_parse_stage", parse)

    current = "full"
    full = await ing._run_ingestion(repo, **KW)
    current = "resume"
    resume = await ing.reparse_for_resume(repo, **KW)

    assert calls == [
        ("traverse", "full"),
        ("parse", "full"),
        ("traverse", "resume"),
        ("parse", "resume"),
    ]
    # Same files, same order, same bytes from either entry point.
    assert [p.file_info.path for p in full[0]] == [p.file_info.path for p in resume[0]]
    assert full[3] == resume[3]
