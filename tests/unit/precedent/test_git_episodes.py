"""Git-tier episodes: one dated change, bound to the files it touched.

Built on real temporary repositories, like the walk they consume: the things
worth asserting here (which commits qualify, what a node set answers when git
is asked about it, what an incremental run must not delete) are git behaviours
with no useful mock.
"""

from __future__ import annotations

import subprocess

from repowise.core.ingestion.git_indexer.prior_defects import FixWalk, collect_fix_commits
from repowise.core.precedent.currency import commits_since
from repowise.core.precedent.git_episodes import (
    KIND_CODE_FIX,
    MAX_EPISODE_NODES,
    derive_git_episodes,
    record_git_episodes,
)
from repowise.core.precedent.store import (
    TIER_GIT,
    TIER_STRUCTURAL,
    Episode,
    EpisodeStore,
    default_store_path,
)


def _repo(tmp_path, *, opted_in: bool = True):
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    repo.config_writer().set_value("user", "name", "T").release()
    if opted_in:
        (tmp_path / ".repowise").mkdir()
    return repo


def _write(repo, tmp_path, name, content, message):
    (tmp_path / name).write_text(content, encoding="utf-8")
    repo.index.add([name])
    return repo.index.commit(message).hexsha


def _walk(tmp_path, paths, *, skip_shas=None) -> FixWalk:
    import git as gitpython

    return collect_fix_commits(
        gitpython.Repo(tmp_path), set(paths), as_of_ts=None, skip_shas=skip_shas
    )


def _rows(tmp_path, **kw) -> list[dict]:
    with EpisodeStore(default_store_path(tmp_path)) as store:
        return store.list_episodes(**kw)


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


class TestSelection:
    def test_a_code_fix_becomes_an_episode(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "def f():\n    return None\n", "feat: add f")
        sha = _write(
            repo,
            tmp_path,
            "a.py",
            "def f():\n    return 0\n",
            "fix: f returned None\n\nCallers were unwrapping it.",
        )

        (episode,) = derive_git_episodes(_walk(tmp_path, ["a.py"]))

        assert episode.tier == TIER_GIT
        assert episode.kind == KIND_CODE_FIX
        assert episode.subject == sha
        assert episode.birth_commit == sha
        assert episode.body.startswith("fix: f returned None")
        assert "Callers were unwrapping it." in episode.body
        assert "changed 1 file together" in episode.evidence

    def test_a_doc_only_fix_produces_nothing(self, tmp_path) -> None:
        """The 53.5% finding, asserted rather than commented.

        The subject rule counts this as a fix. Its diff changes no production
        code, so it is not an episode.
        """
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "README.md", "hello\n", "docs: add readme")
        _write(repo, tmp_path, "README.md", "hello there\n", "fix: wrong wording")

        assert derive_git_episodes(_walk(tmp_path, ["README.md"])) == []

    def test_a_non_fix_commit_is_never_reached(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        _write(repo, tmp_path, "a.py", "x = 2\n", "feat: change a")

        assert derive_git_episodes(_walk(tmp_path, ["a.py"])) == []

    def test_a_sweep_is_skipped_rather_than_truncated(self, tmp_path) -> None:
        """Above the node ceiling nothing is emitted.

        Truncating instead would leave an episode whose staleness question is
        narrower than the change its body describes.
        """
        repo = _repo(tmp_path)
        names = [f"m{i}.py" for i in range(MAX_EPISODE_NODES + 1)]
        for name in names:
            (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
        repo.index.add(names)
        repo.index.commit("feat: add them")
        for name in names:
            (tmp_path / name).write_text("x = 2\n", encoding="utf-8")
        repo.index.add(names)
        repo.index.commit("fix: bump them all")

        assert derive_git_episodes(_walk(tmp_path, names)) == []


class TestBinding:
    def test_nodes_are_the_commit_s_own_paths_and_nothing_else(self, tmp_path) -> None:
        """No directory expansion, unlike a decision record's module scope.

        The reader's scope test is a path prefix match, so adding the parent
        directory would make one fix match every file beneath it.
        """
        repo = _repo(tmp_path)
        (tmp_path / "pkg").mkdir()
        _write(repo, tmp_path, "pkg/a.py", "x = 1\n", "feat: add a")
        (tmp_path / "pkg/b.py").write_text("y = 1\n", encoding="utf-8")
        (tmp_path / "pkg/a.py").write_text("x = 2\n", encoding="utf-8")
        repo.index.add(["pkg/a.py", "pkg/b.py"])
        repo.index.commit("fix: correct both")

        (episode,) = derive_git_episodes(_walk(tmp_path, ["pkg/a.py", "pkg/b.py"]))

        assert episode.nodes == ("pkg/a.py", "pkg/b.py")
        assert "pkg" not in episode.nodes

    def test_the_episode_is_born_at_the_commit_not_at_index_time(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        _write(repo, tmp_path, "a.py", "x = 2\n", "fix: correct a")
        walk = _walk(tmp_path, ["a.py"])

        (episode,) = derive_git_episodes(walk)

        assert episode.birth_at == float(walk.fixes[0].ts)

    def test_its_staleness_question_is_answerable_against_its_birth_sha(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        _write(repo, tmp_path, "b.py", "y = 1\n", "feat: add b")
        _write(repo, tmp_path, "a.py", "x = 2\n", "fix: correct a")

        (episode,) = derive_git_episodes(_walk(tmp_path, ["a.py", "b.py"]))
        scope = list(episode.nodes)

        assert commits_since(tmp_path, since_commit=episode.birth_commit, nodes=scope) == 0
        # A commit elsewhere leaves it standing; one in its scope does not.
        _write(repo, tmp_path, "b.py", "y = 2\n", "feat: change b")
        assert commits_since(tmp_path, since_commit=episode.birth_commit, nodes=scope) == 0
        _write(repo, tmp_path, "a.py", "x = 3\n", "feat: change a")
        assert commits_since(tmp_path, since_commit=episode.birth_commit, nodes=scope) == 1


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_a_repo_that_never_opted_in_grows_no_store(self, tmp_path) -> None:
        repo = _repo(tmp_path, opted_in=False)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        _write(repo, tmp_path, "a.py", "x = 2\n", "fix: correct a")

        assert record_git_episodes(tmp_path, _walk(tmp_path, ["a.py"])) == 0
        assert not default_store_path(tmp_path).exists()

    def test_an_incremental_run_appends_without_deleting_what_came_before(self, tmp_path) -> None:
        """The hazard a kind-scoped sweep would walk straight into.

        An update sees only the commits it has never seen, so a writer that
        made the kind say exactly what this run derived would delete every
        episode the runs before it wrote.
        """
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        first = _write(repo, tmp_path, "a.py", "x = 2\n", "fix: first")
        record_git_episodes(tmp_path, _walk(tmp_path, ["a.py"]))

        second = _write(repo, tmp_path, "a.py", "x = 3\n", "fix: second")
        written = record_git_episodes(tmp_path, _walk(tmp_path, ["a.py"], skip_shas={first}))

        assert written == 1
        assert {row["subject"] for row in _rows(tmp_path, tier=TIER_GIT)} == {first, second}

    def test_an_update_with_no_new_fixes_derives_none_and_deletes_none(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        sha = _write(repo, tmp_path, "a.py", "x = 2\n", "fix: only one")
        record_git_episodes(tmp_path, _walk(tmp_path, ["a.py"]))
        before = _rows(tmp_path, tier=TIER_GIT)

        assert record_git_episodes(tmp_path, _walk(tmp_path, ["a.py"], skip_shas={sha})) == 0
        after = _rows(tmp_path, tier=TIER_GIT)

        assert [row["id"] for row in after] == [row["id"] for row in before]
        assert [row["birth_at"] for row in after] == [row["birth_at"] for row in before]

    def test_episodes_that_fall_out_of_the_window_are_dropped(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        _write(repo, tmp_path, "a.py", "x = 2\n", "fix: aged out")
        walk = _walk(tmp_path, ["a.py"])
        record_git_episodes(tmp_path, walk)
        assert len(_rows(tmp_path, tier=TIER_GIT)) == 1

        # A later walk whose trailing edge has moved past that commit.
        moved = FixWalk(fixes=[], oldest_fix_ts=walk.fixes[0].ts + 1)
        record_git_episodes(tmp_path, moved)

        assert _rows(tmp_path, tier=TIER_GIT) == []

    def test_appending_to_the_git_tier_leaves_the_structural_one_alone(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
        _write(repo, tmp_path, "a.py", "x = 2\n", "fix: correct a")
        with EpisodeStore(default_store_path(tmp_path)) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL,
                kinds=["formatter_drift"],
                episodes=[
                    Episode(
                        tier=TIER_STRUCTURAL,
                        kind="formatter_drift",
                        subject="ruff format",
                        body="This tree is not formatter-clean.",
                        evidence="ruff format --check .",
                    )
                ],
            )

        record_git_episodes(tmp_path, _walk(tmp_path, ["a.py"]))

        assert len(_rows(tmp_path, tier=TIER_STRUCTURAL)) == 1
        assert len(_rows(tmp_path, tier=TIER_GIT)) == 1


# ---------------------------------------------------------------------------
# The indexer wiring
# ---------------------------------------------------------------------------


async def test_a_full_index_records_the_window(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier

    repo = _repo(tmp_path)
    _write(repo, tmp_path, "a.py", "def f():\n    return None\n", "feat: add f")
    _write(repo, tmp_path, "a.py", "def f():\n    return 0\n", "fix: bad return")

    await GitIndexer(tmp_path, tier=GitIndexTier.FULL, record_episodes=True).index_repo("repo1")

    rows = _rows(tmp_path, tier=TIER_GIT)
    assert len(rows) == 1
    assert rows[0]["kind"] == KIND_CODE_FIX
    assert rows[0]["nodes"] == ["a.py"]


def test_the_update_path_records_only_what_it_has_not_seen(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier

    repo = _repo(tmp_path)
    _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
    first = _write(repo, tmp_path, "a.py", "x = 2\n", "fix: first")
    second = _write(repo, tmp_path, "a.py", "x = 3\n", "fix: second")

    indexer = GitIndexer(tmp_path, tier=GitIndexTier.FULL, record_episodes=True)
    indexer.capture_new_fix_events(known_shas={first})

    assert {row["subject"] for row in _rows(tmp_path, tier=TIER_GIT)} == {second}


def test_a_read_only_command_s_indexer_writes_nothing(tmp_path) -> None:
    """`health` and `dead-code` build their own indexer to read metadata.

    They do not pass the repo's exclude patterns, so episodes written there
    would name files the repo excludes and would outlive every prune.
    """
    from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier

    repo = _repo(tmp_path)
    _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
    _write(repo, tmp_path, "a.py", "x = 2\n", "fix: correct a")

    GitIndexer(tmp_path, tier=GitIndexTier.FULL).capture_new_fix_events()

    assert not default_store_path(tmp_path).exists()


def test_only_the_indexing_paths_opt_in() -> None:
    """The gate is a parameter, not a call site, and the opt-in set is pinned.

    Same shape as the environment-fact gate, and for the same reason: a third
    caller flipping this on is a decision to take deliberately.
    """
    import inspect
    from pathlib import Path as _Path

    from repowise.core.ingestion.git_indexer import GitIndexer

    assert inspect.signature(GitIndexer.__init__).parameters["record_episodes"].default is False

    roots = [_Path(__file__).resolve().parents[3] / "packages" / p for p in ("cli", "core", "server")]
    found = {
        path.as_posix().split("/src/")[-1]
        for root in roots
        for path in root.rglob("*.py")
        if "record_episodes=True" in path.read_text(encoding="utf-8", errors="ignore")
    }
    assert found == {
        "repowise/core/pipeline/phases/git.py",
        "repowise/core/pipeline/incremental.py",
    }


def test_git_is_never_asked_a_second_time(tmp_path, monkeypatch) -> None:
    """Derivation is a consumer of the walk, so it spawns nothing of its own."""
    repo = _repo(tmp_path)
    _write(repo, tmp_path, "a.py", "x = 1\n", "feat: add a")
    _write(repo, tmp_path, "a.py", "x = 2\n", "fix: correct a")
    walk = _walk(tmp_path, ["a.py"])

    def _explode(*args, **kwargs):
        raise AssertionError("git episodes must not spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", _explode)
    monkeypatch.setattr(subprocess, "Popen", _explode)

    assert record_git_episodes(tmp_path, walk) == 1
