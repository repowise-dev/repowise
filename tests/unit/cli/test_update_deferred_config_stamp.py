"""A deferred-docs update stamps the config buckets it finished (#5944).

`update --index-only --defer-docs` after a config edit used to leave the
whole `config_fingerprint` unstamped, so every later no-change run re-read
the config as changed and re-indexed the full file set. The buckets the run
did finish (traversal, git, health) are now stamped; only the generation
bucket's hash stays behind, and bucket comparison no longer waits for the
broad fingerprint to move. Excluded paths are likewise dropped from the
patch gate's inputs before they can force a full rebuild.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.cli.commands.update_cmd.config_changes import (
    classify_config_changes,
    retain_stale_buckets,
)
from repowise.cli.helpers import config_fingerprint, load_config, save_state
from repowise.core.repo_config import config_dependency_fingerprints


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    (repo / ".repowise").mkdir()
    save_state(repo, {"docs_mode": "none"})
    return repo


def test_a_stale_generation_bucket_reads_as_changed_without_a_broad_change(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    current = config_dependency_fingerprints(repo)
    # The deferred-docs run: the broad fingerprint and every bucket except
    # generation were stamped; generation keeps the pre-edit hash.
    state = {
        "config_fingerprint": config_fingerprint(repo),
        "config_dependency_fingerprints": retain_stale_buckets(
            {"generation": "pre-edit"}, current, ("generation",)
        ),
    }
    changes = classify_config_changes(repo, state, load_config(repo))
    # The broad fingerprint matches, so the cheap detector sees nothing...
    assert not changes.changed
    # ...but the stale generation bucket is exactly the unfinished work.
    assert changes.generation_changed
    assert not changes.traversal_changed
    assert not changes.git_changed
    assert not changes.health_changed


def test_retain_stale_buckets_keeps_only_the_named_buckets() -> None:
    stored = {"generation": "old-generation", "traversal": "old-traversal"}
    current = {"generation": "new-generation", "traversal": "new-traversal"}
    stamped = retain_stale_buckets(stored, current, ("generation",))
    assert stamped == {
        "generation": "old-generation",
        "traversal": "new-traversal",
    }
    # Without stale buckets, or without stored hashes to fall back on,
    # the current fingerprints pass through untouched.
    assert retain_stale_buckets(stored, current, ()) == current
    assert retain_stale_buckets(None, current, ("generation",)) == current
    assert retain_stale_buckets({}, current, ("generation",)) == current


def test_excluded_paths_cannot_reach_the_patch_gate(tmp_path: Path) -> None:
    from repowise.core.pipeline.incremental.graph import (
        _excluded_path_filter,
        build_filtered_changed_paths,
    )

    patterns = [".sqlx/", "*.sha384"]
    excluded = _excluded_path_filter(patterns)
    assert excluded(".sqlx/query-abc.json")
    assert excluded("checksum.sha384")
    assert not _excluded_path_filter([])("anything")
    assert not excluded("src/main.py")

    class _Diff:
        def __init__(self, path: str) -> None:
            self.path = path

    kept = build_filtered_changed_paths(
        [_Diff(".sqlx/query-abc.json"), _Diff("src/main.py")], patterns
    )
    assert kept == ["src/main.py"]


@pytest.mark.parametrize("stale", [(), ("generation",)])
def test_a_fully_stamped_config_reads_as_unchanged(tmp_path: Path, stale: tuple) -> None:
    repo = _repo(tmp_path)
    current = config_dependency_fingerprints(repo)
    state = {
        "config_fingerprint": config_fingerprint(repo),
        "config_dependency_fingerprints": retain_stale_buckets(
            {bucket: "pre-edit" for bucket in stale}, current, stale
        ),
    }
    changes = classify_config_changes(repo, state, load_config(repo))
    assert not changes.changed
    assert changes.generation_changed is bool(stale)
