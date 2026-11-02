"""A re-score blames only the Split File candidates stored without commit sets."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.ingestion.git_indexer.function_blame import BlameIndex
from repowise.core.pipeline.resume.rehydrate import attach_commit_set_blame


def _parsed(tmp_path: Path, path: str, *, lines: int, symbols: int):
    abs_path = tmp_path / path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text("x = 1\n" * lines, encoding="utf-8")
    return SimpleNamespace(
        file_info=SimpleNamespace(path=path, abs_path=str(abs_path), language="python"),
        symbols=[SimpleNamespace(kind="function", parent_name=None) for _ in range(symbols)],
    )


@pytest.fixture
def blamed(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def _blame(self: GitIndexer, paths):
        calls.append(list(paths))
        return {p: BlameIndex(lines={1: ("a", 1)}) for p in paths}

    monkeypatch.setattr(GitIndexer, "blame_indexes", _blame)
    return calls


def test_only_candidates_without_stored_sets_are_blamed(tmp_path: Path, blamed) -> None:
    parsed = [
        _parsed(tmp_path, "big.py", lines=400, symbols=10),
        _parsed(tmp_path, "stored.py", lines=400, symbols=10),
        _parsed(tmp_path, "small.py", lines=100, symbols=10),
        _parsed(tmp_path, "few.py", lines=400, symbols=3),
        _parsed(tmp_path, "young.py", lines=400, symbols=10),
        _parsed(tmp_path, "tests/test_big.py", lines=400, symbols=10),
    ]
    meta = {pf.file_info.path: {"commit_count_total": 9} for pf in parsed}
    meta["stored.py"]["function_commit_shas"] = [("f", 1, 2, ["a"])]
    meta["young.py"]["commit_count_total"] = 2

    assert attach_commit_set_blame(tmp_path, meta, parsed, git_tier="full") == 1
    assert blamed == [["big.py"]]
    assert isinstance(meta["big.py"]["commit_set_blame"], BlameIndex)
    assert "commit_set_blame" not in meta["stored.py"]


def test_a_tier_without_blame_blames_nothing(tmp_path: Path, blamed) -> None:
    parsed = [_parsed(tmp_path, "big.py", lines=400, symbols=10)]
    meta = {"big.py": {"commit_count_total": 9}}
    assert attach_commit_set_blame(tmp_path, meta, parsed, git_tier="essential") == 0
    assert blamed == []
    assert "commit_set_blame" not in meta["big.py"]
