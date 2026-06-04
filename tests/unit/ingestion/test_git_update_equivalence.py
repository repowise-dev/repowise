"""Update-path git indexing must produce the same per-file metadata as init.

``index_changed_files`` now reads from the same batched repo-wide commit
index as ``index_repo`` (instead of one ``git log -- <file>`` per changed
file), so an updated file's metadata — including the agent-provenance
rollup, which the old per-file path never classified — must match what a
fresh full index produces.
"""

from __future__ import annotations

import threading

import pytest

from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.ingestion.git_indexer.tiers import GitIndexTier

# Fields produced by BOTH the full-index and changed-file paths. Excludes
# full-index-only enrichment (co-change, entropy, percentiles, hotspot flags)
# and the float temporal score (compared separately with a tolerance).
_COMPARED_FIELDS = [
    "commit_count_total",
    "commit_count_90d",
    "commit_count_30d",
    "first_commit_at",
    "last_commit_at",
    "age_days",
    "primary_owner_name",
    "primary_owner_email",
    "top_authors_json",
    "significant_commits_json",
    "commit_categories_json",
    "lines_added_90d",
    "lines_deleted_90d",
    "contributor_count",
    "bus_factor",
    "prior_defect_count",
    "agent_commit_count",
    "agent_authored_pct",
    "agent_tier_counts_json",
]


def _build_repo(tmp_path):
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")

    (tmp_path / "a.py").write_text("x = 1\n")
    repo.index.add(["a.py"])
    repo.index.commit("feat: add module a with the initial implementation")

    (tmp_path / "b.py").write_text("y = 2\n")
    repo.index.add(["b.py"])
    repo.index.commit("feat: add module b for the second subsystem")

    (tmp_path / "a.py").write_text("x = 1\nz = 3\n")
    repo.index.add(["a.py"])
    # Agent-attributed commit (co-author trailer channel).
    repo.index.commit(
        "fix: correct the boundary condition in module a\n\n"
        "Co-Authored-By: Claude <noreply@anthropic.com>"
    )

    (tmp_path / "b.py").write_text("y = 2\nw = 4\n")
    repo.index.add(["b.py"])
    repo.index.commit("fix: resolve crash in module b error handling")
    repo.close()


async def test_changed_file_metadata_matches_full_index(tmp_path) -> None:
    _build_repo(tmp_path)

    full = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    _summary, full_meta = await full.index_repo("repo1")
    full_by_path = {m["file_path"]: m for m in full_meta}

    upd = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    upd_meta = await upd.index_changed_files(["a.py", "b.py"])
    upd_by_path = {m["file_path"]: m for m in upd_meta}

    assert set(upd_by_path) == {"a.py", "b.py"}
    for path in ("a.py", "b.py"):
        for field in _COMPARED_FIELDS:
            assert upd_by_path[path].get(field) == full_by_path[path].get(field), (
                f"{path}: field {field!r} diverges between update and init"
            )
        assert upd_by_path[path]["temporal_hotspot_score"] == pytest.approx(
            full_by_path[path]["temporal_hotspot_score"], rel=1e-3
        )


async def test_update_path_classifies_agent_provenance(tmp_path) -> None:
    """Regression: the per-file fallback never classified provenance, so
    updates silently zeroed a file's agent rollup."""
    _build_repo(tmp_path)

    upd = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    upd_meta = await upd.index_changed_files(["a.py"])
    (meta,) = upd_meta

    assert meta["agent_commit_count"] == 1
    assert meta["agent_authored_pct"] == pytest.approx(0.5)
    assert meta["agent_tier_counts_json"] != "{}"


def test_thread_repo_pool_reuses_per_thread_and_closes(tmp_path) -> None:
    _build_repo(tmp_path)
    indexer = GitIndexer(tmp_path)
    get_repo, close_all = indexer._thread_repo_pool()

    first = get_repo()
    assert get_repo() is first  # same thread → same handle

    seen_other: list = []

    def _worker() -> None:
        seen_other.append(get_repo())

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    assert seen_other and seen_other[0] is not first  # new thread → new handle

    close_all()
    # close() released the handles; a fresh pool hands out new ones.
    get_repo2, close_all2 = indexer._thread_repo_pool()
    assert get_repo2() is not first
    close_all2()
