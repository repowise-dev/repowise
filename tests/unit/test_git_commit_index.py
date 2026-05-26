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
            + "\x1f"
            + c.get("body", "")
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


def test_load_commit_index_captures_multiline_body() -> None:
    """A multi-line commit body must be captured intact, not mistaken for numstat."""
    body = "## Why\nWe migrate to Redis because the in-process cache could not\nshare state across workers.\n\nCloses #42"
    raw = _build_log(
        [
            {
                "sha": "aaa",
                "an": "Alice",
                "ae": "a@x.com",
                "ct": int(time.time()),
                "subj": "feat: adopt Redis",
                "body": body,
                "files": [(5, 1, "src/a.py")],
            },
        ]
    )
    repo = MagicMock()
    repo.git.log.return_value = raw

    index = load_commit_index(repo, 100, {"src/a.py"})

    rec = index["src/a.py"][0]
    # Numstat churn still parsed correctly despite the multi-line body.
    assert rec.added == 5
    assert rec.deleted == 1
    # Body retained verbatim (whitespace-stripped), including the "Closes #" line.
    assert "migrate to Redis" in rec.body
    assert "Closes #42" in rec.body
    # The numstat row must NOT have leaked into the body.
    assert "src/a.py" not in rec.body


def test_log_format_arg_has_no_real_null_byte() -> None:
    """Regression for #226: the --format ARG must use git's ``%x00`` escape
    text, never a real NUL byte. A real NUL in a subprocess argument raises
    ``ValueError: embedded null character`` on every repo, which silently
    killed the batched commit index. Git expands ``%x00``/``%x1f`` to the real
    separator bytes in its *output*, which is what the parser splits on.
    """
    from repowise.core.ingestion.git_indexer import _FIELD_SEP, _LOG_FORMAT, _RECORD_SEP

    assert "\x00" not in _LOG_FORMAT
    assert "\x1f" not in _LOG_FORMAT
    assert "%x00" in _LOG_FORMAT
    assert "%x1f" in _LOG_FORMAT
    # The parser still splits on the real bytes git emits.
    assert _RECORD_SEP == "\x00"
    assert _FIELD_SEP == "\x1f"


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
