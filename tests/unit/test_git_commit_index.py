"""Unit tests for the batched repo-wide commit index loader."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from repowise.core.ingestion.git_commit_index import load_commit_index


def _build_log(commits: list[dict]) -> str:
    """Render an in-memory list of commit dicts into the raw `git log` format.

    Each dict shape: {sha, an, ae, ct, parents, subj, files: [(added, deleted, path), ...]}.
    """
    lines: list[str] = []
    for c in commits:
        header = (
            "\x00"
            + c["sha"]
            + "\x1f"
            + c["an"]
            + "\x1f"
            + c["ae"]
            + "\x1f"
            + str(c["ct"])
            + "\x1f"
            + c.get("parents", "")
            + "\x1f"
            + c["subj"]
        )
        lines.append(header)
        for added, deleted, path in c["files"]:
            lines.append(f"{added}\t{deleted}\t{path}")
    return "\n".join(lines)


def test_load_commit_index_buckets_per_file() -> None:
    now = int(time.time())
    raw = _build_log(
        [
            {
                "sha": "aaa",
                "an": "Alice",
                "ae": "a@x.com",
                "ct": now,
                "subj": "feat: thing",
                "files": [(5, 1, "src/a.py"), (3, 0, "src/b.py")],
            },
            {
                "sha": "bbb",
                "an": "Bob",
                "ae": "b@x.com",
                "ct": now - 3600,
                "subj": "fix: stuff",
                "files": [(2, 4, "src/a.py")],
            },
        ]
    )
    repo = MagicMock()
    repo.git.log.return_value = raw

    index = load_commit_index(repo, 100, {"src/a.py", "src/b.py"})

    assert set(index.keys()) == {"src/a.py", "src/b.py"}
    assert len(index["src/a.py"]) == 2
    assert index["src/a.py"][0].sha == "aaa"
    assert index["src/a.py"][0].added == 5
    assert index["src/a.py"][1].added == 2
    assert len(index["src/b.py"]) == 1


def test_load_commit_index_drops_non_indexable_files() -> None:
    raw = _build_log(
        [
            {
                "sha": "aaa",
                "an": "Alice",
                "ae": "a@x.com",
                "ct": int(time.time()),
                "subj": "mix",
                "files": [(5, 1, "src/a.py"), (1, 0, "docs/skip.md")],
            },
        ]
    )
    repo = MagicMock()
    repo.git.log.return_value = raw

    # docs/skip.md is not in the indexable set — it must be dropped.
    index = load_commit_index(repo, 100, {"src/a.py"})

    assert "docs/skip.md" not in index
    assert "src/a.py" in index


def test_load_commit_index_returns_empty_on_failure() -> None:
    repo = MagicMock()
    repo.git.log.side_effect = RuntimeError("git not available")

    assert load_commit_index(repo, 100, {"a.py"}) == {}


def test_load_commit_index_handles_renames() -> None:
    """Rename markers ``{old => new}`` must be attributed to the new path."""
    raw = _build_log(
        [
            {
                "sha": "aaa",
                "an": "Alice",
                "ae": "a@x.com",
                "ct": int(time.time()),
                "subj": "rename",
                "files": [(10, 5, "src/{old.py => new.py}")],
            }
        ]
    )
    repo = MagicMock()
    repo.git.log.return_value = raw

    index = load_commit_index(repo, 100, {"src/new.py", "src/old.py"})
    # Churn attributed to the new path
    assert "src/new.py" in index
    assert index["src/new.py"][0].added == 10
