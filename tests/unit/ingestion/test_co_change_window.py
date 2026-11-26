"""``windowed_partners``: co-change partners of one file over commits since a date."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from repowise.core.ingestion.git_indexer.co_change import windowed_partners


def _git(root: Path, *args: str, date: str | None = None) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "A",
        "GIT_AUTHOR_EMAIL": "a@example.com",
        "GIT_COMMITTER_NAME": "A",
        "GIT_COMMITTER_EMAIL": "a@example.com",
        "HOME": str(root),
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    if date:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", "-C", str(root), *args], env=env, check=True, capture_output=True)


def _commit(root: Path, files: list[str], date: str) -> None:
    for path in files:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(f"{date}\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", date, date=f"{date}T12:00:00")


def _repo(root: Path) -> set[str]:
    _git(root, "init", "-q")
    # Old history: the target changed with its test many times.
    for day in range(1, 9):
        _commit(root, ["src/a.py", "tests/test_a.py"], f"2024-01-{day:02d}")
    # The window: the target changes with b.py three times, c.py twice, d.py once.
    _commit(root, ["src/a.py", "src/b.py", "src/c.py"], "2024-06-01")
    _commit(root, ["src/a.py", "src/b.py", "src/d.py"], "2024-06-02")
    _commit(root, ["src/a.py", "src/b.py", "src/c.py"], "2024-06-03")
    # A pair that never touches the target does not count for it.
    _commit(root, ["src/b.py", "src/c.py"], "2024-06-04")
    return {"src/a.py", "src/b.py", "src/c.py", "src/d.py", "tests/test_a.py"}


def test_counts_only_the_targets_commits_in_the_window(tmp_path):
    files = _repo(tmp_path)

    records, commits = windowed_partners(str(tmp_path), "src/a.py", "2024-05-01", files)

    assert commits == 3
    assert [(r["file_path"], r["frequency"]) for r in records] == [
        ("src/b.py", 3),
        ("src/c.py", 2),
    ]
    # One shared commit is below the support floor; old history is outside the window.
    assert all(r["self_commits"] == 3 for r in records)
    assert records[0]["last_co_change"] == "2024-06-03"
    assert records[0]["co_change_count"] == 3.0


def test_a_window_reaching_back_counts_older_history(tmp_path):
    files = _repo(tmp_path)

    records, commits = windowed_partners(str(tmp_path), "src/a.py", "2023-12-01", files)

    assert commits == 11
    assert records[0]["file_path"] == "tests/test_a.py"
    assert records[0]["frequency"] == 8


def test_a_path_the_index_lacks_is_never_a_partner(tmp_path):
    files = _repo(tmp_path) - {"src/b.py"}

    records, _ = windowed_partners(str(tmp_path), "src/a.py", "2024-05-01", files)

    assert [r["file_path"] for r in records] == ["src/c.py"]


def test_no_checkout_returns_none(tmp_path):
    assert windowed_partners(str(tmp_path), "src/a.py", "2024-05-01", {"src/a.py"}) is None


def test_an_index_below_the_git_top_level_reads_its_own_paths(tmp_path):
    """The index names files from its checkout; git's top level is one up."""
    _git(tmp_path, "init", "-q")
    sub = tmp_path / "svc"
    for day in ("2024-06-01", "2024-06-02"):
        _commit(tmp_path, ["svc/src/a.py", "svc/src/b.py", "other/x.py"], day)

    records, commits = windowed_partners(
        str(sub), "src/a.py", "2024-05-01", {"src/a.py", "src/b.py"}
    )

    assert commits == 2
    assert [(r["file_path"], r["frequency"]) for r in records] == [("src/b.py", 2)]


def test_a_target_with_glob_characters_is_read_as_a_path(tmp_path):
    _git(tmp_path, "init", "-q")
    for day in ("2024-06-01", "2024-06-02"):
        _commit(tmp_path, ["app/[id]/page.tsx", "app/lib.ts"], day)
        # ``[id]`` read as a glob would match this file too.
        _commit(tmp_path, ["app/i/page.tsx", "app/other.ts"], day)

    records, commits = windowed_partners(
        str(tmp_path),
        "app/[id]/page.tsx",
        "2024-05-01",
        {"app/[id]/page.tsx", "app/lib.ts", "app/i/page.tsx", "app/other.ts"},
    )

    assert commits == 2
    assert [r["file_path"] for r in records] == ["app/lib.ts"]
