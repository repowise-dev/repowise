"""Non-code files get a history tier: counts, span and authors from the repo-wide walk.

Code files keep the full pass (blame, churn signals). Binary, vendored and lock
files get no row. History-tier rows stay out of the repo-relative rankings
(the SQL recompute is covered in ``tests/unit/persistence``).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.ingestion.git_indexer.tiers import GitIndexTier

_PY = "".join(f"def f{i}(x):\n    return x + {i}\n\n" for i in range(12))


def _git(root: Path, *args: str, date: str | None = None, author: str = "Alice") -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": f"{author.lower()}@example.com",
        "GIT_COMMITTER_NAME": author,
        "GIT_COMMITTER_EMAIL": f"{author.lower()}@example.com",
        "HOME": str(root),
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    if date:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    out = subprocess.run(
        ["git", "-C", str(root), *args], env=env, check=True, capture_output=True, text=True
    )
    return out.stdout


def _commit(root: Path, files: dict[str, str | bytes], msg: str, day: int, author: str) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
    _git(root, "add", "--", *files)
    _git(root, "commit", "-q", "-m", msg, date=f"2024-03-{day:02d}T12:00:00", author=author)


def _build(root: Path) -> None:
    _git(root, "init", "-q")
    _commit(
        root,
        {
            "a.py": _PY,
            "b.py": "y = 1\n",
            "README.md": "# r\n",
            ".github/workflows/ci.yml": "on: push\n",
            "config.json": "{}\n",
            "logo.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00binary",
            "yarn.lock": "# lock\n",
            "vendor/lib/notes.md": "vendored\n",
            "dist/report.md": "built\n",
        },
        "feat: initial",
        1,
        "Alice",
    )
    for i in range(2, 6):
        _commit(
            root,
            {"a.py": _PY + f"# {i}\n", "README.md": f"# r {i}\n", "logo.png": b"\x00" * i},
            f"feat: round {i}",
            i,
            "Bob" if i % 2 else "Alice",
        )
    _commit(root, {"config.json": '{"k": 1}\n'}, "chore: config", 7, "Carol")
    _commit(root, {".github/workflows/ci.yml": "on: [push]\n"}, "ci: tweak", 8, "Bob")


def _git_log_count(root: Path, path: str) -> int:
    return len(_git(root, "log", "--no-merges", "--format=%H", "--", path).split())


async def test_non_code_files_get_history_from_the_shared_walk(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_GIT_WINDOW_ANCHOR", "head")
    _build(tmp_path)

    _summary, rows = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("r")
    meta = {row["file_path"]: row for row in rows}

    # Binary, lock, vendored and build-output files get no row at all.
    assert set(meta) == {"a.py", "b.py", "README.md", ".github/workflows/ci.yml", "config.json"}

    for path in ("README.md", ".github/workflows/ci.yml", "config.json"):
        row = meta[path]
        assert row["history_only"] is True
        # The count git itself reports for the path, exactly.
        assert row["commit_count_total"] == _git_log_count(tmp_path, path)
        assert row["first_commit_at"] is not None and row["last_commit_at"] is not None
        # Blame and the code signals stay code-only.
        assert row["primary_owner_line_pct"] is None
        assert "blame_index" not in row
        assert row["temporal_hotspot_score"] == 0.0
        assert row["churn_percentile"] == 0.0 and row["is_hotspot"] is False
        assert json.loads(row["co_change_partners_json"]) == []

    readme = meta["README.md"]
    assert readme["commit_count_total"] == 5
    authors = {a["name"]: a["commit_count"] for a in json.loads(readme["top_authors_json"])}
    assert authors == {"Alice": 3, "Bob": 2}
    assert readme["contributor_count"] == 2
    assert meta["config.json"]["last_commit_at"].date().isoformat() == "2024-03-07"

    # Code rows keep the full pass and rank among code files only.
    assert meta["a.py"]["history_only"] is False
    assert meta["a.py"]["commit_count_total"] == _git_log_count(tmp_path, "a.py")
    assert meta["a.py"]["primary_owner_line_pct"] is not None
    assert {meta["a.py"]["churn_percentile"], meta["b.py"]["churn_percentile"]} <= {0.0, 0.5}


async def test_update_writes_the_same_history_rows_as_a_full_index(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_GIT_WINDOW_ANCHOR", "head")
    _build(tmp_path)
    tracked = set(_git(tmp_path, "ls-files").split())

    sink: dict[str, dict] = {}
    rows = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_changed_files(
        ["README.md", "logo.png", "yarn.lock", "a.py"],
        all_files=tracked,
        co_change_sink={},
        idle_decay_sink=sink,
    )
    _summary, full_rows = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("r")
    full = {row["file_path"]: row for row in full_rows}

    changed = {row["file_path"]: row for row in rows}
    assert set(changed) == {"a.py", "README.md"}
    for key in ("history_only", "commit_count_total", "top_authors_json", "last_commit_at"):
        assert changed["README.md"][key] == full["README.md"][key]
    assert set(sink) <= set(full)
    # Idle history-tier rows refresh their windows but carry no code signal.
    for path in ("config.json", ".github/workflows/ci.yml"):
        assert sink[path]["temporal_hotspot_score"] == 0.0
        assert sink[path]["prior_defect_count"] == 0


async def test_follow_renames_mode_gives_non_code_files_no_rows(tmp_path, monkeypatch) -> None:
    """Pins a known limit: --follow skips the shared walk the history tier fills from."""
    monkeypatch.setenv("REPOWISE_GIT_WINDOW_ANCHOR", "head")
    _build(tmp_path)

    _s, rows = await GitIndexer(
        tmp_path, tier=GitIndexTier.FULL, follow_renames=True
    ).index_repo("r")
    paths = {row["file_path"] for row in rows}

    assert "a.py" in paths
    assert not {"README.md", ".github/workflows/ci.yml", "config.json"} & paths


def test_a_source_package_named_like_an_output_dir_keeps_its_history(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer.records import _history_tier_files

    for rel in ("pkg/coverage/parsers.py", "pkg/coverage/README.md", "dist/notes.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n", encoding="utf-8")

    kept = _history_tier_files(tmp_path, ["pkg/coverage/README.md", "dist/notes.md"])

    assert kept == {"pkg/coverage/README.md"}
