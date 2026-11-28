"""Content-baseline change detection for an index with no git commit."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.change_detector import ChangeDetector
from repowise.core.ingestion.content_baseline import (
    advance_baseline,
    load_baseline,
    save_baseline,
    snapshot,
    snapshot_digest,
)


def _tree(root: Path) -> None:
    (root / ".repowise").mkdir(parents=True)
    (root / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")


def test_snapshot_diff_reports_added_modified_and_deleted(tmp_path: Path) -> None:
    _tree(tmp_path)
    before = snapshot(tmp_path)
    assert set(before) == {"a.py", "b.py"}

    (tmp_path / "a.py").write_text("def gamma():\n    return 3\n", encoding="utf-8")
    (tmp_path / "b.py").unlink()
    (tmp_path / "c.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    diffs = ChangeDetector(tmp_path).get_content_changes(before, snapshot(tmp_path))

    by_path = {d.path: d for d in diffs}
    assert {p: d.status for p, d in by_path.items()} == {
        "a.py": "modified",
        "b.py": "deleted",
        "c.py": "added",
    }
    assert [s.name for s in by_path["a.py"].new_parsed.symbols] == ["gamma"]


def test_an_unchanged_tree_has_no_diffs_and_a_stable_digest(tmp_path: Path) -> None:
    _tree(tmp_path)
    first, second = snapshot(tmp_path), snapshot(tmp_path)

    assert ChangeDetector(tmp_path).get_content_changes(first, second) == []
    assert snapshot_digest(first) == snapshot_digest(second)


def test_a_run_that_did_not_persist_everything_keeps_the_old_baseline(tmp_path: Path) -> None:
    _tree(tmp_path)
    save_baseline(tmp_path, {"a.py": "old"})

    advance_baseline(tmp_path, None, {"a.py": "new"}, clean=False)
    assert load_baseline(tmp_path) == {"a.py": "old"}

    advance_baseline(tmp_path, None, {"a.py": "new"}, clean=True)
    assert load_baseline(tmp_path) == {"a.py": "new"}


def test_a_clean_run_at_a_commit_drops_the_baseline(tmp_path: Path) -> None:
    _tree(tmp_path)
    save_baseline(tmp_path, {"a.py": "old"})

    advance_baseline(tmp_path, "a" * 40, {"a.py": "new"}, clean=True)

    assert load_baseline(tmp_path) is None


def test_a_run_that_diffed_commits_leaves_the_baseline_alone(tmp_path: Path) -> None:
    _tree(tmp_path)
    save_baseline(tmp_path, {"a.py": "old"})

    advance_baseline(tmp_path, "a" * 40, None, clean=True)

    assert load_baseline(tmp_path) == {"a.py": "old"}


def test_a_corrupt_baseline_reads_as_none(tmp_path: Path) -> None:
    _tree(tmp_path)
    (tmp_path / ".repowise" / "content_baseline.json").write_text("[1, 2]", encoding="utf-8")

    assert load_baseline(tmp_path) is None
