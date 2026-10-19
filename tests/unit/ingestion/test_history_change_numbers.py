"""The PR or MR number stored on a file's significant commits, read per forge."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.core.analysis.decisions.commit_mining import pr_commit_block
from repowise.core.forges import ForgeKind, get_forge
from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.ingestion.git_indexer.file_history import _significant_entry
from repowise.core.ingestion.git_indexer.records import _CommitRec


def _rec(subject: str, body: str = "") -> _CommitRec:
    return _CommitRec("a" * 40, "Dev", "dev@example.com", 1_700_000_000, False, subject, body)


@pytest.mark.parametrize(
    ("forge", "subject", "body", "expected"),
    [
        (ForgeKind.GITHUB, "fix(x): y (#881) (#911)", "", 911),
        (ForgeKind.GITHUB, "Fixes #12: crash on start", "", None),
        (ForgeKind.GITHUB, "feat: add JVM PKCS#12 helper", "", None),
        (ForgeKind.GITLAB, "feat: thing (!17)", "", 17),
        (ForgeKind.GITHUB, "feat: thing (!17)", "", None),
        (ForgeKind.GITLAB, "fix: a", "See merge request g/p!42", 42),
        # A squash suffix past the 200 characters the stored message keeps.
        (ForgeKind.GITHUB, "docs: " + "x" * 220 + " (#10154)", "", 10154),
    ],
)
def test_the_stored_pr_number_is_the_change_the_commit_merged(
    forge: ForgeKind, subject: str, body: str, expected: int | None
) -> None:
    entry = _significant_entry(_rec(subject, body), subject[:200], forge)
    assert entry.get("pr_number") == expected


def test_the_indexer_reads_its_forge_from_the_remote(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@gitlab.com:g/sub/p.git"],
        check=True,
    )
    assert GitIndexer(tmp_path).forge is ForgeKind.GITLAB


@pytest.mark.parametrize(
    ("forge", "label"),
    [
        (ForgeKind.GITHUB, "(#12)"),
        (ForgeKind.GITLAB, "(!12)"),
        (ForgeKind.AZURE, "(PR 12)"),
        (ForgeKind.BITBUCKET, "(#12)"),
    ],
)
def test_the_pr_prompt_labels_the_number_as_the_forge_writes_it(
    forge: ForgeKind, label: str
) -> None:
    candidate = {"sha": "b" * 40, "subject": "s", "body": "b", "pr": 12}
    block = pr_commit_block(candidate, ["a.py"], get_forge(forge))
    assert f"--- Commit bbbbbbbb {label} ---" in block
    no_pr = pr_commit_block({**candidate, "pr": None}, ["a.py"], get_forge(forge))
    assert "--- Commit bbbbbbbb ---" in no_pr
