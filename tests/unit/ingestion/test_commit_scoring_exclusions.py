"""Scoring exclusions must survive the real git phase without hiding files."""
from __future__ import annotations

import pytest

from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.pipeline.phases.git import _run_git_indexing
from repowise.core.repo_config import (
    changed_config_dependencies,
    config_dependency_fingerprints,
    save_repo_config,
)


@pytest.mark.parametrize(
    ("patterns", "docs_lines", "mixed_lines", "mixed_files"),
    [([], 20, 4, 3), (["docs/**"], 0, 1, 1), (["docs/**", "!docs/keep.md"], 0, 3, 2)],
)
async def test_scoring_config_full_phase_and_capture_agree(
    tmp_path, patterns, docs_lines, mixed_lines, mixed_files
) -> None:
    import git

    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test Author")
        config.set_value("user", "email", "author@example.com")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/plan.md").write_text("plan\n" * 20, encoding="utf-8")
    repo.index.add(["docs/plan.md"])
    docs_sha = repo.index.commit("docs: record plan").hexsha
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs/plan.md").write_text("plan\n" * 21, encoding="utf-8")
    (tmp_path / "docs/keep.md").write_text("keep\n" * 2, encoding="utf-8")
    repo.index.add(["src/main.py", "docs/plan.md", "docs/keep.md"])
    mixed_sha = repo.index.commit("feat: add code with docs").hexsha
    save_repo_config(tmp_path, {"commit_scoring_exclude_patterns": patterns})

    summary, metadata, _ = await _run_git_indexing(
        tmp_path, commit_depth=100, follow_renames=False, progress=None
    )
    assert summary is not None
    rows = {row["sha"]: row for row in summary.commit_rows}
    assert rows[docs_sha]["lines_added"] == docs_lines
    assert rows[docs_sha]["files_changed"] == (1 if docs_lines else 0)
    assert rows[mixed_sha]["lines_added"] == mixed_lines
    assert rows[mixed_sha]["files_changed"] == mixed_files
    # The scoring filter must never become a traversal filter.
    assert {row["file_path"] for row in metadata} >= {"src/main.py", "docs/plan.md", "docs/keep.md"}
    assert {row["file_path"] for row in summary.commit_file_rows} >= {"docs/plan.md", "docs/keep.md"}

    detail = []
    capture = GitIndexer(tmp_path, commit_scoring_exclude_patterns=patterns)
    captured = {row["sha"]: row for row in capture.capture_new_commit_rows(file_rows_sink=detail)}
    for sha in rows:
        for field in ("lines_added", "lines_deleted", "files_changed", "entropy", "change_risk_score"):
            assert captured[sha][field] == rows[sha][field]
    assert {row["file_path"] for row in detail} >= {"docs/plan.md", "docs/keep.md"}
    repo.close()


def test_scoring_config_add_and_remove_invalidate_git_not_traversal(tmp_path) -> None:
    save_repo_config(tmp_path, {})
    before = config_dependency_fingerprints(tmp_path)
    save_repo_config(tmp_path, {"commit_scoring_exclude_patterns": ["docs/**"]})
    excluded = config_dependency_fingerprints(tmp_path)
    assert changed_config_dependencies(before, excluded) == {"git_history"}
    save_repo_config(tmp_path, {})
    assert changed_config_dependencies(excluded, config_dependency_fingerprints(tmp_path)) == {"git_history"}
