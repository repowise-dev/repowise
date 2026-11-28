"""A depot mirror's changelist trailer stays out of stored and prompted commit text."""

from __future__ import annotations

import subprocess
from pathlib import Path

import git
import pytest

from repowise.core.analysis.decisions.commit_mining import git_commit_block
from repowise.core.ingestion.git_indexer.file_history import (
    _parse_per_file_log,
    _significant_entry,
)
from repowise.core.ingestion.git_indexer.records import (
    _LOG_FORMAT,
    _RECORD_SEP,
    _parse_commit_record,
)

_GIT_P4 = '[git-p4: depot-paths = "//depot/main/": change = {n}: options = ktext]'
_P4_FUSION = '[p4-fusion: depot-paths = "//depot/main/": change = {n}]'


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _commit(repo: Path, name: str, message: str) -> None:
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)


@pytest.fixture
def mirror(tmp_path: Path) -> Path:
    repo = tmp_path / "mirror"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(
        repo,
        "alloc.c",
        "Replace the allocator with a pool\n\n"
        "Decided to pool allocations instead of malloc per frame.\n\n"
        f"{_GIT_P4.format(n=4400)}",
    )
    # One line and no blank line before the trailer: git folds it into %s.
    _commit(repo, "cache.c", f"4401 - Tune the cache\n{_P4_FUSION.format(n=4401)}")
    return repo


def _headers(repo: Path) -> dict[str, dict]:
    raw = _git(repo, "log", "--numstat", f"--format={_LOG_FORMAT}")
    parsed = [_parse_commit_record(r) for r in raw.split(_RECORD_SEP) if r.strip()]
    return {h["changelist"]: h for h, _ in parsed if h}


def test_a_one_line_p4_fusion_subject_is_stored_without_its_trailer(mirror: Path) -> None:
    # The raw subject git prints carries the trailer.
    assert "p4-fusion" in _git(mirror, "log", "-1", "--format=%s")
    fusion = _headers(mirror)["4401"]
    assert fusion["subject"] == "4401 - Tune the cache"
    assert fusion["body"] == ""


def test_a_git_p4_trailer_is_cut_from_the_body_and_its_number_kept(mirror: Path) -> None:
    git_p4 = _headers(mirror)["4400"]
    assert git_p4["subject"] == "Replace the allocator with a pool"
    assert git_p4["body"] == "Decided to pool allocations instead of malloc per frame."


def test_a_decision_mining_prompt_carries_no_trailer(mirror: Path) -> None:
    commits, _ = _parse_per_file_log(
        git.Repo(mirror), "alloc.c", commit_limit=10, follow_renames=False
    )
    assert commits
    entry = _significant_entry(commits[0], commits[0].subject[:200])
    assert entry.get("body")
    assert entry["changelist"] == "4400"
    block = git_commit_block(entry, entry["body"], ["alloc.c"])
    assert "Decided to pool allocations" in block
    assert "git-p4" not in block
    assert "depot-paths" not in block


def test_a_change_risk_row_shows_the_subject_without_its_trailer(mirror: Path) -> None:
    from repowise.core.analysis.change_risk.features import extract_commit_features

    head = _git(mirror, "rev-parse", "HEAD").strip()
    assert extract_commit_features(str(mirror), head).subject == "4401 - Tune the cache"
