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


def _build_repo_with_cochange(tmp_path):
    """Repo where a.py and b.py change together three times (recent)."""
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")

    for i in range(3):
        (tmp_path / "a.py").write_text(f"x = {i}\n")
        (tmp_path / "b.py").write_text(f"y = {i}\n")
        repo.index.add(["a.py", "b.py"])
        repo.index.commit(f"feat: joint change {i} touching both modules")
    repo.close()


async def test_update_path_recomputes_co_change_partners(tmp_path) -> None:
    """Regression: index_changed_files left co_change_partners_json at its
    empty default, and the field-by-field upsert then wiped the partners
    computed at init for every file an update touched — i.e. the hotspots."""
    import json

    _build_repo_with_cochange(tmp_path)

    upd = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    upd_meta = await upd.index_changed_files(["a.py"], all_files={"a.py", "b.py"})
    (meta,) = upd_meta

    partners = json.loads(meta["co_change_partners_json"])
    assert partners, "co-change walk must repopulate partners on update"
    assert partners[0]["file_path"] == "b.py"
    assert meta["change_entropy"] > 0.0


async def test_update_path_essential_tier_skips_co_change_walk(tmp_path) -> None:
    _build_repo_with_cochange(tmp_path)

    upd = GitIndexer(tmp_path, tier=GitIndexTier.ESSENTIAL)
    upd_meta = await upd.index_changed_files(["a.py"], all_files={"a.py", "b.py"})
    (meta,) = upd_meta

    # ESSENTIAL defers the walk; the upsert guard preserves any existing DB
    # value, so the metadata dict itself stays at the empty default.
    assert meta["co_change_partners_json"] == "[]"


# ---------------------------------------------------------------------------
# Idle-file decay refresh (#728): an update must rewrite the time-decayed
# history fields for *unchanged* files too, or their scores can only ratchet
# downward and never recover as the anchor advances.
# ---------------------------------------------------------------------------


def _commit_dated(repo, tmp_path, files: dict[str, str], msg: str, date: str):
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    repo.index.add(list(files))
    return repo.index.commit(msg, author_date=date, commit_date=date)


def _build_repo_idle(tmp_path):
    """a.py + b.py churn together, then only b.py keeps changing."""
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    for i in range(3):
        _commit_dated(
            repo,
            tmp_path,
            {"a.py": "x\n" * (i + 2), "b.py": f"y = {i}\n"},
            f"feat: joint change {i} touching both modules",
            "2024-01-01T00:00:00",
        )
    for i in range(4):
        _commit_dated(
            repo, tmp_path, {"b.py": f"y = {i + 9}\n"}, f"chore: b only {i}", "2024-01-05T00:00:00"
        )
    repo.close()


async def test_idle_decay_refresh_matches_full_index(tmp_path, monkeypatch) -> None:
    """The idle refresh must write exactly what a fresh full index would — the
    correct decayed value — for an unchanged file, using decay-only keys."""
    import json

    from repowise.core.ingestion.git_indexer.file_history import DECAY_REFRESH_KEYS

    monkeypatch.setenv("REPOWISE_GIT_WINDOW_ANCHOR", "head")
    _build_repo_idle(tmp_path)

    full = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    _summary, full_meta = await full.index_repo("repo1")
    full_by_path = {m["file_path"]: m for m in full_meta}

    sink: dict[str, dict] = {}
    upd = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    changed = await upd.index_changed_files(
        ["b.py"], all_files={"a.py", "b.py"}, idle_decay_sink=sink
    )

    # Only idle files land in the sink; changed files stay on the normal path.
    assert set(sink) == {"a.py"}
    assert {m["file_path"] for m in changed} == {"b.py"}

    idle = sink["a.py"]
    # Decay-only: never carries full-history columns the upsert would clobber.
    assert set(idle) == set(DECAY_REFRESH_KEYS) | {"file_path"}

    full_a = full_by_path["a.py"]
    assert idle["temporal_hotspot_score"] == pytest.approx(
        full_a["temporal_hotspot_score"], rel=1e-3
    )
    assert idle["commit_count_90d"] == full_a["commit_count_90d"]
    assert idle["lines_added_90d"] == full_a["lines_added_90d"]
    assert json.loads(idle["co_change_partners_json"]) == json.loads(
        full_a["co_change_partners_json"]
    )


async def test_idle_decay_refresh_recovers_as_anchor_advances(tmp_path, monkeypatch) -> None:
    """The reported symptom: leave a churny file alone and its decayed score
    must shrink as new (unrelated) commits advance the anchor."""
    import git as gitpython

    monkeypatch.setenv("REPOWISE_GIT_WINDOW_ANCHOR", "head")

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    # a.py churns hard in January.
    for i in range(4):
        _commit_dated(
            repo, tmp_path, {"a.py": "x\n" * (i + 5)}, f"feat: churn a {i}", "2024-01-01T00:00:00"
        )
    _commit_dated(repo, tmp_path, {"b.py": "y = 0\n"}, "feat: add b", "2024-01-02T00:00:00")
    repo.close()

    # Score a.py at the January anchor.
    early = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    _s, early_meta = await early.index_repo("repo1")
    early_a = {m["file_path"]: m for m in early_meta}["a.py"]["temporal_hotspot_score"]

    # Six months later, only b.py changes — a.py is idle.
    repo = gitpython.Repo(tmp_path)
    for i in range(3):
        _commit_dated(
            repo, tmp_path, {"b.py": f"y = {i + 1}\n"}, f"chore: b {i}", "2024-07-01T00:00:00"
        )
    repo.close()

    sink: dict[str, dict] = {}
    upd = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    await upd.index_changed_files(["b.py"], all_files={"a.py", "b.py"}, idle_decay_sink=sink)

    assert sink["a.py"]["temporal_hotspot_score"] < early_a, (
        "idle file's decayed score must recover (shrink) as the anchor advances"
    )


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
