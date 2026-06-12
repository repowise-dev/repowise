"""Tests for the deep commit-index walk that replaces per-file log fallbacks.

Files the recent-window commit index never saw used to each spawn a
per-file ``git log`` subprocess. ``load_deep_commit_index`` walks the
older history region once (``--skip``) and buckets those files' commits;
``index_repo`` consults it before falling back per file.

For rename-free linear history the deep bucket must be IDENTICAL to what
the per-file fallback produces (same shas, order, churn, provenance), so
the swap is exercised end to end against the fallback as oracle.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.git_commit_index import (
    load_commit_index,
    load_deep_commit_index,
)
from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.ingestion.git_indexer.file_history import _parse_per_file_log
from repowise.core.ingestion.git_indexer.tiers import GitIndexTier


def _build_repo(tmp_path, *, old_files: int = 3, recent_commits: int = 2):
    """Linear history: each old file gets two commits, then `recent_commits`
    commits touch only ``recent.py`` — so a window of that size covers only
    ``recent.py`` and every old file lands beyond the window."""
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")

    for i in range(old_files):
        p = tmp_path / f"old_{i}.py"
        p.write_text(f"x{i} = 1\n")
        repo.index.add([p.name])
        repo.index.commit(f"feat: add old module {i} with the initial implementation")
        p.write_text(f"x{i} = 1\ny{i} = 2\n")
        repo.index.add([p.name])
        if i == 0:
            # Agent-attributed commit in the DEEP region — the rollup must
            # survive the batched path.
            repo.index.commit(
                f"fix: adjust old module {i}\n\n"
                "Co-Authored-By: Claude <noreply@anthropic.com>"
            )
        else:
            repo.index.commit(f"fix: adjust old module {i} boundary handling")

    recent = tmp_path / "recent.py"
    for j in range(recent_commits):
        recent.write_text(f"r = {j}\n")
        repo.index.add(["recent.py"])
        repo.index.commit(f"feat: iterate on the recent module step {j}")
    repo.close()


def _rec_tuple(c):
    return (c.sha, c.ts, c.is_merge, c.added, c.deleted, c.agent, c.agent_tier)


def test_deep_bucket_matches_per_file_fallback(tmp_path) -> None:
    _build_repo(tmp_path)
    import git as gitpython

    repo = gitpython.Repo(tmp_path)
    indexable = {"old_0.py", "old_1.py", "old_2.py", "recent.py"}
    window = load_commit_index(repo, 2, indexable)
    assert set(window) == {"recent.py"}

    missed = indexable - set(window)
    deep = load_deep_commit_index(
        repo, 2, missed, skip=2, deep_limit=20_000
    )
    assert set(deep) == missed

    for fp in sorted(missed):
        oracle, _orig = _parse_per_file_log(
            repo,
            fp,
            commit_limit=2,
            follow_renames=False,
            provenance_classifier=None,
        )
        # The oracle path classifies provenance only when given a classifier;
        # compare structure first, then provenance separately below.
        assert [(c.sha, c.ts, c.is_merge, c.added, c.deleted) for c in deep[fp]] == [
            (c.sha, c.ts, c.is_merge, c.added, c.deleted) for c in oracle
        ], fp

    # Agent provenance was classified during the deep walk.
    agents = [c.agent for c in deep["old_0.py"]]
    assert any(agents), agents
    repo.close()


def test_deep_bucket_respects_per_file_cap(tmp_path) -> None:
    _build_repo(tmp_path)
    import git as gitpython

    repo = gitpython.Repo(tmp_path)
    deep = load_deep_commit_index(
        repo, 1, {"old_0.py", "old_1.py"}, skip=2, deep_limit=20_000
    )
    assert all(len(v) == 1 for v in deep.values())
    # Newest first: the cap keeps each file's most recent deep commit.
    assert all(v[0].subject.startswith("fix:") for v in deep.values())
    repo.close()


def test_deep_bucket_only_contains_wanted_files(tmp_path) -> None:
    _build_repo(tmp_path)
    import git as gitpython

    repo = gitpython.Repo(tmp_path)
    deep = load_deep_commit_index(
        repo, 2, {"old_1.py"}, skip=2, deep_limit=20_000
    )
    assert set(deep) == {"old_1.py"}
    repo.close()


def test_empty_wanted_set_skips_the_walk(tmp_path) -> None:
    _build_repo(tmp_path)
    import git as gitpython

    repo = gitpython.Repo(tmp_path)
    assert load_deep_commit_index(repo, 2, set(), skip=2, deep_limit=20_000) == {}
    repo.close()


async def test_index_repo_uses_deep_index_instead_of_per_file_logs(
    tmp_path, monkeypatch
) -> None:
    """With the deep walk active, files beyond the window must get their
    metadata WITHOUT any per-file git log, and that metadata must equal the
    per-file path's output (rename-free history → semantics align)."""
    _build_repo(tmp_path)
    # Anchor recency windows to HEAD so the two runs share a reference
    # "now" (otherwise the decayed temporal score drifts at the 1e-9 level).
    monkeypatch.setenv("REPOWISE_GIT_WINDOW_ANCHOR", "head")

    # Reference run: deep walk disabled (threshold impossible to reach),
    # so old files take the per-file fallback exactly as before.
    monkeypatch.setattr(
        "repowise.core.ingestion.git_indexer.indexer._DEEP_WALK_MIN_FALLBACK", 10**9
    )
    ref = GitIndexer(tmp_path, commit_limit=2, tier=GitIndexTier.ESSENTIAL)
    _s, ref_meta = await ref.index_repo("r1")
    ref_by_path = {m["file_path"]: m for m in ref_meta}

    # Deep run: threshold 1 forces the deep walk; per-file fallback must
    # never fire for files the deep bucket covers.
    monkeypatch.setattr(
        "repowise.core.ingestion.git_indexer.indexer._DEEP_WALK_MIN_FALLBACK", 1
    )
    calls: list[str] = []
    import repowise.core.ingestion.git_indexer.file_history as fh

    orig = fh._parse_per_file_log

    def _spy(repo, file_path, **kwargs):
        calls.append(file_path)
        return orig(repo, file_path, **kwargs)

    monkeypatch.setattr(fh, "_parse_per_file_log", _spy)

    deep_run = GitIndexer(tmp_path, commit_limit=2, tier=GitIndexTier.ESSENTIAL)
    _s2, deep_meta = await deep_run.index_repo("r1")
    deep_by_path = {m["file_path"]: m for m in deep_meta}

    assert calls == [], f"per-file fallback fired for {calls}"
    assert set(deep_by_path) == set(ref_by_path)
    skip_fields = {"churn_percentile", "change_entropy_pct"}  # repo-relative, identical anyway
    for fp, ref_m in ref_by_path.items():
        deep_m = deep_by_path[fp]
        for field, want in ref_m.items():
            if field in skip_fields:
                continue
            got = deep_m.get(field)
            if isinstance(want, float):
                assert got == pytest.approx(want, rel=1e-9), (fp, field)
            else:
                assert got == want, (fp, field)
