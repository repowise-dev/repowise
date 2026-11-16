"""Lifetime churn folds onto its anchor, and refuses to when that is unsafe.

``capture_repo_totals`` used to re-walk the whole history on every update to
recompute a total that had moved by one commit. It now resumes from the commit
the last capture recorded. The property every test here asserts is the same
one: **a folded capture equals a from-scratch capture, byte for byte.** The
interesting cases are the histories where folding would be wrong, and each of
those has to fall back rather than produce a plausible number.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess

import pytest

from repowise.core.ingestion.git_indexer.records import (
    _CHURN_REANCHOR_STRIDE as _STRIDE,
)
from repowise.core.ingestion.git_indexer.records import (
    RepoTotals,
    _tz_offset_minutes,
    capture_repo_totals,
)


def _git(path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=str(path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return out.stdout.strip()


def _init(path):
    import git as gitpython

    repo = gitpython.Repo.init(path)
    repo.config_writer().set_value("user", "name", "Ada Lovelace").release()
    repo.config_writer().set_value("user", "email", "ada@example.com").release()
    return repo


def _commit(repo, path, name: str, content: str) -> None:
    (path / name).write_text(content, encoding="utf-8")
    repo.index.add([name])
    repo.index.commit(f"feat: {name} -> {len(content)}")


def _churn(totals: RepoTotals) -> tuple[int | None, int | None]:
    return totals.total_lines_added, totals.total_lines_deleted


def _fresh(repo) -> RepoTotals:
    """A capture with no prior: the whole-history walk, i.e. the ground truth."""
    return capture_repo_totals(repo)


def test_scoped_totals_include_deleted_files_but_not_other_subflows(tmp_path) -> None:
    repo = _init(tmp_path)
    (tmp_path / "selected").mkdir()
    (tmp_path / "other").mkdir()
    _commit(repo, tmp_path, "other/a.py", "outside = 1\n" * 10)
    _commit(repo, tmp_path, "selected/deleted.py", "inside = 1\n" * 3)
    first_selected = repo.head.commit
    repo.index.remove(["selected/deleted.py"], working_tree=True)
    repo.index.commit("remove selected file")
    (tmp_path / "selected").mkdir(exist_ok=True)
    _commit(repo, tmp_path, "selected/current.py", "current = 1\n")

    totals = capture_repo_totals(repo, pathspecs=("selected",))

    assert totals.total_commit_count == 3
    assert _churn(totals) == (4, 3)
    assert totals.first_commit_at == first_selected.committed_datetime
    assert totals.first_commit_subject == first_selected.summary
    assert totals.total_contributor_count == 1
    assert capture_repo_totals(repo, totals) == capture_repo_totals(repo)


def test_indexer_and_traversal_share_declared_scope_and_exclusions(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.ingestion.traverser import FileTraverser
    from repowise.core.repo_config import analysis_git_pathspecs, save_repo_config

    repo = _init(tmp_path)
    (tmp_path / "selected").mkdir()
    (tmp_path / "other").mkdir()
    _commit(repo, tmp_path, "other/a.py", "outside = 1\n" * 10)
    _commit(repo, tmp_path, "selected/a.py", "inside = 1\n" * 3)
    _commit(repo, tmp_path, "selected/skip.py", "excluded = 1\n" * 20)
    save_repo_config(tmp_path, {
        "analysis_paths": ["selected"], "exclude_patterns": ["skip.py"],
    })

    indexer = GitIndexer(tmp_path, exclude_patterns=["skip.py"])
    assert indexer._get_tracked_files(repo) == ["selected/a.py"]
    totals = indexer.capture_repo_totals(capture_repo_totals(repo))
    file_rows: list[dict] = []
    rows = indexer.capture_new_commit_rows(file_rows_sink=file_rows)

    assert _churn(totals) == (3, 0)
    assert totals.total_commit_count == 1
    assert len(rows) == 1
    assert {row["file_path"] for row in file_rows} == {"selected/a.py"}
    assert {info.path for info in FileTraverser(
        tmp_path, extra_exclude_patterns=["skip.py"],
    ).traverse()} == {"selected/a.py"}
    assert totals.history_pathspecs == analysis_git_pathspecs({
        "analysis_paths": ["selected"], "exclude_patterns": ["skip.py"],
    })


def test_incremental_paths_do_not_escape_declared_scope(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    (tmp_path / "selected").mkdir()
    (tmp_path / "other").mkdir()
    _commit(repo, tmp_path, "selected/a.py", "inside = 1\n")
    _commit(repo, tmp_path, "other/b.py", "outside = 1\n")
    save_repo_config(tmp_path, {"analysis_paths": ["selected"]})
    indexer = GitIndexer(tmp_path)

    scope = indexer._changed_scope(repo, ["other/b.py"], None)

    assert scope.changed == []
    assert not scope.history_changed


def test_incremental_paths_respect_exclusions_without_explicit_scope(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    _commit(repo, tmp_path, "skip.py", "excluded\n")
    save_repo_config(tmp_path, {"exclude_patterns": ["skip.py"]})

    scope = GitIndexer(tmp_path)._changed_scope(repo, ["skip.py"], {"skip.py"})

    assert scope.changed == []
    assert not scope.history_changed


def test_incremental_history_matches_full_scoped_history_after_rename(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    (tmp_path / "selected").mkdir()
    _commit(repo, tmp_path, "selected/old.py", "included\n")
    _git(tmp_path, "mv", "selected/old.py", "selected/new.py")
    repo.index.commit("rename")
    save_repo_config(tmp_path, {"analysis_paths": ["selected"]})
    indexer = GitIndexer(tmp_path)
    target = "selected/new.py"

    full = indexer._full_commit_index(repo, [target], set(), [], None, None, None)
    incremental, fallback = indexer._changed_commit_index(repo, {target}, False, set(), None, None, None)

    assert fallback == full.fallback_files
    assert incremental.get(target, []) == full.commits.get(target, [])


@pytest.mark.asyncio
async def test_empty_current_scope_keeps_deleted_history(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    (tmp_path / "selected").mkdir()
    _commit(repo, tmp_path, "selected/gone.py", "inside = 1\n")
    repo.index.remove(["selected/gone.py"], working_tree=True)
    repo.index.commit("delete selected")
    save_repo_config(tmp_path, {"analysis_paths": ["selected"]})

    summary, rows = await GitIndexer(tmp_path).index_repo("test-repo")

    assert rows == []
    assert summary.repo_totals.total_commit_count == 2
    assert len(summary.commit_rows) == 2


def test_subdirectory_scope_is_relative_to_indexed_directory(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    (tmp_path / "src").mkdir()
    module = tmp_path / "svc"
    (module / "src").mkdir(parents=True)
    _commit(repo, tmp_path, "src/outside.py", "outside = 1\n" * 7)
    _commit(repo, tmp_path, "svc/src/inside.py", "inside = 1\n")
    save_repo_config(module, {"analysis_paths": ["src"]})
    indexer = GitIndexer(module)

    assert indexer._get_tracked_files(repo) == ["svc/src/inside.py"]
    assert _churn(indexer.capture_repo_totals()) == (1, 0)


def test_nested_scope_rooted_exclusions_handle_glob_characters_in_prefix(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    module = tmp_path / "svc[1]"
    (module / "src").mkdir(parents=True)
    _commit(repo, tmp_path, "svc[1]/src/inside.py", "inside = 1\n")
    _commit(repo, tmp_path, "svc[1]/src/skip.py", "excluded = 1\n" * 4)
    save_repo_config(module, {"analysis_paths": ["src"], "exclude_patterns": ["/src/skip.py"]})

    totals = GitIndexer(module).capture_repo_totals()

    assert totals.total_commit_count == 1
    assert _churn(totals) == (1, 0)


def test_exclusions_limit_whole_repository_history(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    _commit(repo, tmp_path, "inside.py", "inside = 1\n")
    _commit(repo, tmp_path, "skip.py", "excluded = 1\n" * 4)
    save_repo_config(tmp_path, {"exclude_patterns": ["skip.py"]})

    totals = GitIndexer(tmp_path).capture_repo_totals()

    assert totals.total_commit_count == 1
    assert _churn(totals) == (1, 0)


def test_whole_repository_exclusions_allow_reincluded_history(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.ingestion.traverser import FileTraverser
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    (tmp_path / "generated").mkdir()
    _commit(repo, tmp_path, "generated/drop.py", "excluded = 1\n" * 4)
    _commit(repo, tmp_path, "generated/keep.py", "included = 1\n")
    save_repo_config(tmp_path, {
        "exclude_patterns": ["generated/**", "!generated/keep.py"],
    })

    totals = GitIndexer(tmp_path).capture_repo_totals()

    assert totals.total_commit_count == 1
    assert _churn(totals) == (1, 0)
    assert {file.path for file in FileTraverser(
        tmp_path, extra_exclude_patterns=["generated/**", "!generated/keep.py"],
    ).traverse()} == {"generated/keep.py"}


def test_excluded_parent_prevents_history_reinclusion(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.ingestion.traverser import FileTraverser
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    (tmp_path / "generated").mkdir()
    _commit(repo, tmp_path, "generated/keep.py", "included\n")
    patterns = ["generated/", "!generated/keep.py"]
    save_repo_config(tmp_path, {"exclude_patterns": patterns})
    indexer = GitIndexer(tmp_path)

    assert not list(FileTraverser(tmp_path, extra_exclude_patterns=patterns).traverse())
    assert indexer._get_tracked_files(repo) == []
    assert indexer.capture_repo_totals().total_commit_count == 0


def test_recursive_directory_only_exclusion_keeps_root_files(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.ingestion.traverser import FileTraverser
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    (tmp_path / "foo" / "sub").mkdir(parents=True)
    _commit(repo, tmp_path, "foo/a.py", "included\n")
    _commit(repo, tmp_path, "foo/sub/b.py", "excluded\n")
    patterns = ["foo/**/"]
    save_repo_config(tmp_path, {"exclude_patterns": patterns})
    indexer = GitIndexer(tmp_path)

    assert {file.path for file in FileTraverser(
        tmp_path, extra_exclude_patterns=patterns,
    ).traverse()} == {"foo/a.py"}
    assert indexer._get_tracked_files(repo) == ["foo/a.py"]
    assert indexer.capture_repo_totals().total_commit_count == 1


def test_scoped_history_filters_mixed_commits_and_deleted_reincluded_files(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    (tmp_path / "selected" / "generated").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    _commit(repo, tmp_path, "selected/generated/drop.py", "excluded\n" * 4)
    _commit(repo, tmp_path, "other/outside.py", "outside\n" * 9)
    (tmp_path / "selected/generated/drop.py").write_text("excluded\n" * 6, encoding="utf-8")
    (tmp_path / "selected/generated/keep.py").write_text("included\n", encoding="utf-8")
    repo.index.add(["selected/generated/drop.py", "selected/generated/keep.py"])
    repo.index.commit("mixed")
    repo.index.remove(["selected/generated/keep.py"], working_tree=True)
    repo.index.commit("deleted included file")
    save_repo_config(tmp_path, {
        "analysis_paths": ["selected"],
        "exclude_patterns": ["selected/generated/**", "!selected/generated/keep.py"],
    })
    indexer = GitIndexer(tmp_path)
    file_rows: list[dict] = []

    totals = indexer.capture_repo_totals()
    commits = indexer.capture_new_commit_rows(file_rows_sink=file_rows)

    assert totals.total_commit_count == 2
    assert _churn(totals) == (1, 1)
    assert {row["subject"] for row in commits} == {"mixed", "deleted included file"}
    assert {row["file_path"] for row in file_rows} == {"selected/generated/keep.py"}
    assert len(indexer.list_reachable_shas()) == 2


def test_all_history_excluded_is_empty_not_whole_repository(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    _commit(repo, tmp_path, "excluded.py", "not selected\n")
    save_repo_config(tmp_path, {"exclude_patterns": ["*"]})
    indexer = GitIndexer(tmp_path)

    totals = indexer.capture_repo_totals()

    assert totals.total_commit_count == 0
    assert _churn(totals) == (0, 0)
    assert indexer.capture_new_commit_rows() == []
    assert indexer.list_reachable_shas() == set()


@pytest.mark.parametrize("name", ["slot", "slot]", "é", "slot\n"])
def test_exclusion_compression_handles_a_file_becoming_a_directory(tmp_path, name) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    for ordinary_name in ("a.py", "b.py", "c.py"):
        _commit(repo, tmp_path, ordinary_name, "included\n")
    _commit(repo, tmp_path, name, "excluded\n")
    repo.index.remove([name], working_tree=True)
    repo.index.commit("remove excluded file")
    (tmp_path / name).mkdir()
    _commit(repo, tmp_path, f"{name}/keep.py", "included\n")
    save_repo_config(tmp_path, {"exclude_patterns": [f"/{name}", f"!/{name}/", f"!/{name}/keep.py"]})

    totals = GitIndexer(tmp_path).capture_repo_totals()

    assert totals.total_commit_count == 4
    assert _churn(totals) == (4, 0)


def test_history_scope_reloads_when_head_changes(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    _commit(repo, tmp_path, "excluded.py", "excluded\n")
    save_repo_config(tmp_path, {"exclude_patterns": ["excluded.py"]})
    indexer = GitIndexer(tmp_path)
    assert indexer.capture_repo_totals().total_commit_count == 0

    _commit(repo, tmp_path, "included.py", "included\n")
    assert indexer.capture_repo_totals().total_commit_count == 1


@pytest.mark.parametrize("surface", ["totals", "commits", "reachable"])
def test_history_scope_does_not_include_a_new_excluded_file_during_capture(
    tmp_path, monkeypatch, surface
) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    _commit(repo, tmp_path, "included.py", "included\n")
    save_repo_config(tmp_path, {"exclude_patterns": ["*.tmp"]})
    indexer = GitIndexer(tmp_path)
    original = indexer._git_analysis_pathspecs

    def move_head(*args, **kwargs):
        result = original(*args, **kwargs)
        _commit(repo, tmp_path, "excluded.tmp", "excluded\n")
        return result

    monkeypatch.setattr(indexer, "_git_analysis_pathspecs", move_head)
    if surface == "totals":
        totals = indexer.capture_repo_totals()
        assert totals.total_commit_count == 1
        assert _churn(totals) == (1, 0)
    elif surface == "commits":
        assert len(indexer.capture_new_commit_rows()) == 1
    else:
        assert len(indexer.list_reachable_shas()) == 1


@pytest.mark.asyncio
async def test_exclusions_do_not_break_an_unborn_repository(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    _init(tmp_path)
    save_repo_config(tmp_path, {"exclude_patterns": ["vendor/"]})

    summary, rows = await GitIndexer(tmp_path).index_repo("test-repo")

    assert rows == []
    assert summary.files_indexed == 0


def test_history_pathspecs_support_newlines_in_names(tmp_path) -> None:
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.repo_config import save_repo_config

    repo = _init(tmp_path)
    _commit(repo, tmp_path, "odd\nname.py", "included\n")
    _commit(repo, tmp_path, "excluded.py", "excluded\n" * 4)
    save_repo_config(tmp_path, {"exclude_patterns": ["excluded.py"]})

    totals = GitIndexer(tmp_path).capture_repo_totals()

    assert totals.total_commit_count == 1
    assert _churn(totals) == (1, 0)


@pytest.mark.parametrize("paths", ["selected", ["../escape"], ["/absolute"], ["selected/*"]])
def test_analysis_scope_rejects_nonliteral_boundaries(paths) -> None:
    from repowise.core.repo_config import RepoConfigError, analysis_paths

    with pytest.raises(RepoConfigError):
        analysis_paths({"analysis_paths": paths})


# ---------------------------------------------------------------------------
# The fold itself
# ---------------------------------------------------------------------------


def test_folded_capture_equals_a_full_recapture(tmp_path) -> None:
    repo = _init(tmp_path)
    _commit(repo, tmp_path, "a.py", "x = 1\n")
    _commit(repo, tmp_path, "b.py", "y = 2\ny = 3\n")

    first = capture_repo_totals(repo)
    assert first.churn_anchor_sha == repo.head.commit.hexsha
    assert _churn(first) == (3, 0)

    _commit(repo, tmp_path, "c.py", "z = 4\nz = 5\nz = 6\n")
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    repo.index.add(["a.py"])
    repo.index.commit("chore: empty a")

    folded = capture_repo_totals(repo, first)
    assert folded == _fresh(repo)
    assert _churn(folded) == (6, 1)
    assert folded.churn_anchor_sha == repo.head.commit.hexsha


def test_an_unmoved_head_re_affirms_the_stored_pair(tmp_path) -> None:
    """The no-op update: same HEAD, no range to walk, same answer."""
    repo = _init(tmp_path)
    _commit(repo, tmp_path, "a.py", "x = 1\n")

    first = capture_repo_totals(repo)
    assert capture_repo_totals(repo, first) == _fresh(repo)


class _SpyGit:
    """Records the rev every ``--shortstat`` log is asked for, passes the rest on.

    A wrapper rather than a monkeypatch: GitPython's ``Git`` dispatches command
    names through ``__getattr__`` over ``__slots__``, so there is no ``log``
    attribute to replace.
    """

    def __init__(self, git, revs: list[str]) -> None:
        self._git, self._revs = git, revs

    def log(self, *args, **kwargs):
        if "--shortstat" in args:
            self._revs.append(args[-1])
        return self._git.log(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._git, name)


class _SpyRepo:
    def __init__(self, repo, revs: list[str]) -> None:
        self._repo = repo
        self.git = _SpyGit(repo.git, revs)

    def __getattr__(self, name):
        return getattr(self._repo, name)


def test_a_fold_never_runs_the_whole_history_walk(tmp_path) -> None:
    """Pins the point of the change: the second capture must not walk from root.

    Without this, every assertion above still passes on an implementation that
    quietly re-walks and ignores the anchor.
    """
    repo = _init(tmp_path)
    _commit(repo, tmp_path, "a.py", "x = 1\n")
    first = capture_repo_totals(repo)
    _commit(repo, tmp_path, "b.py", "y = 2\n")

    revs: list[str] = []
    capture_repo_totals(_SpyRepo(repo, revs), first)

    assert revs == [f"{first.churn_anchor_sha}..{repo.head.commit.hexsha}"]


# ---------------------------------------------------------------------------
# The histories where folding would be wrong
# ---------------------------------------------------------------------------


def test_a_rewritten_history_falls_back_to_the_full_walk(tmp_path) -> None:
    """The anchor stops being an ancestor: rebase, force-push, branch swap."""
    repo = _init(tmp_path)
    _commit(repo, tmp_path, "a.py", "x = 1\n")
    _commit(repo, tmp_path, "b.py", "y = 2\ny = 3\n")
    keep = repo.head.commit.hexsha
    _commit(repo, tmp_path, "c.py", "z = 4\nz = 5\nz = 6\nz = 7\n")

    anchored = capture_repo_totals(repo)
    assert _churn(anchored) == (7, 0)

    # Drop the anchored commit and build a different one in its place.
    _git(tmp_path, "reset", "--hard", keep)
    _commit(repo, tmp_path, "d.py", "w = 8\n")

    folded = capture_repo_totals(repo, anchored)
    truth = _fresh(repo)
    assert folded == truth
    # The whole point: 4 added, not the 8 a blind fold would have reported.
    assert _churn(folded) == (4, 0)


def test_a_rebased_branch_in_the_history_falls_back(tmp_path) -> None:
    """A real rebase, then more work on top of it.

    The acceptance case for the phase: the anchor was captured on the pre-rebase
    history, so the fold must refuse, and the refusal must land on the same
    numbers a from-scratch capture produces.
    """
    repo = _init(tmp_path)
    _commit(repo, tmp_path, "base.py", "b = 1\n")
    base = repo.head.commit.hexsha
    _commit(repo, tmp_path, "main1.py", "m = 1\nm = 2\n")
    trunk = repo.active_branch.name  # whatever init.defaultBranch names it

    _git(tmp_path, "checkout", "-q", "-b", "feature", base)
    _commit(repo, tmp_path, "feat1.py", "f = 1\nf = 2\nf = 3\n")

    anchored = capture_repo_totals(repo)  # anchored on the pre-rebase feature tip
    assert _churn(anchored) == (4, 0)

    _git(tmp_path, "rebase", "-q", trunk)
    revs: list[str] = []
    folded = capture_repo_totals(_SpyRepo(repo, revs), anchored)

    assert folded == _fresh(repo)
    assert _churn(folded) == (6, 0)
    # Asserting only the number would pass on an implementation that never
    # folds at all. This asserts the *refusal*: a walk from the root, not a
    # range off the stale anchor.
    assert revs == [repo.head.commit.hexsha]

    # And the capture after the rebase is a usable anchor again.
    _commit(repo, tmp_path, "feat2.py", "g = 1\n")
    assert capture_repo_totals(repo, folded) == _fresh(repo)


def test_deepening_a_shallow_clone_falls_back_to_the_full_walk(tmp_path) -> None:
    """The case only the commit-count check can see.

    The anchor stays a perfectly good ancestor of HEAD and ``anchor..HEAD`` does
    not change; what changed is the history *underneath* it. An ancestry check
    alone would fold and drop the newly fetched churn silently, forever.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    repo = _init(origin)
    # The middle commit *rewrites* a line, so lifetime churn (which counts every
    # commit's diff) and the shallow tip's single diff-against-nothing disagree.
    # Without that they coincide and the test proves nothing.
    _commit(repo, origin, "a.py", "x = 1\nx = 2\nx = 3\n")
    _commit(repo, origin, "a.py", "x = 1\nx = 9\nx = 3\n")
    _commit(repo, origin, "c.py", "z = 1\n")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "file://" + str(origin).replace("\\", "/"), str(clone)],
        capture_output=True,
        text=True,
        check=True,
    )

    import git as gitpython

    shallow = gitpython.Repo(clone)
    anchored = capture_repo_totals(shallow)
    # Only the fetched tip is visible, and it diffs against nothing, so
    # "lifetime" churn is the whole tree at one commit deep.
    assert anchored.total_commit_count == 1
    assert _churn(anchored) == (4, 0)

    _git(clone, "fetch", "-q", "--unshallow")
    revs: list[str] = []
    folded = capture_repo_totals(_SpyRepo(shallow, revs), anchored)

    assert revs == [shallow.head.commit.hexsha]  # refused, not folded
    assert folded == _fresh(shallow)
    assert folded.total_commit_count == 3
    # HEAD never moved, so a fold that trusted ancestry alone would still be
    # reporting the shallow tip's 4.
    assert _churn(folded) == (5, 1)
    shallow.close()
    repo.close()


def test_a_skipped_churn_walk_stores_no_anchor(tmp_path, monkeypatch) -> None:
    """Above the ceiling there is no churn figure, so there must be no anchor.

    An anchor written without the totals it anchors is the one way this can go
    quietly wrong: the next capture would add a range onto a base that never
    covered its start.
    """
    monkeypatch.setattr(
        "repowise.core.ingestion.git_indexer.records._CHURN_COMMIT_CEILING", 0
    )
    repo = _init(tmp_path)
    _commit(repo, tmp_path, "a.py", "x = 1\n")

    totals = capture_repo_totals(repo)
    assert _churn(totals) == (None, None)
    assert totals.churn_anchor_sha is None


def test_a_prior_without_churn_totals_is_not_folded(tmp_path) -> None:
    """A hand-edited or half-written row must not be treated as a base."""
    repo = _init(tmp_path)
    _commit(repo, tmp_path, "a.py", "x = 1\nx = 2\n")
    head = repo.head.commit.hexsha

    for broken in (
        RepoTotals(total_commit_count=1, total_lines_added=None, churn_anchor_sha=head),
        RepoTotals(total_commit_count=None, total_lines_added=99, total_lines_deleted=0,
                   churn_anchor_sha=head),
        RepoTotals(total_commit_count=1, total_lines_added=99, total_lines_deleted=0),
    ):
        assert capture_repo_totals(repo, broken) == _fresh(repo)


def test_a_retroactive_gitattributes_change_is_corrected_by_the_stride(tmp_path) -> None:
    """The failure no ancestry or count check can see, and its bound.

    ``git log --shortstat`` resolves diff attributes from the working tree, so
    committing ``gen.min.js -diff`` retroactively changes what an *already
    anchored* commit contributed. The commit graph does not move, the counts
    still reconcile, and a fold therefore reports a number that is not just
    wrong but becomes the next fold's base. Only the periodic re-walk recovers.
    """
    repo = _init(tmp_path)
    (tmp_path / "gen.min.js").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    repo.index.add(["gen.min.js"])
    repo.index.commit("feat: generated")
    _commit(repo, tmp_path, "b.txt", "x\n")

    anchored = capture_repo_totals(repo)
    assert _churn(anchored) == (6, 0)

    (tmp_path / ".gitattributes").write_text("gen.min.js -diff\n", encoding="utf-8")
    repo.index.add([".gitattributes"])
    repo.index.commit("chore: stop diffing the bundle")

    drifted = capture_repo_totals(repo, anchored)
    truth = _fresh(repo)
    assert _churn(truth) == (2, 0)
    assert _churn(drifted) == (7, 0), "documents the drift the stride exists to bound"

    # Crossing the stride boundary re-walks and lands on the truth again.
    assert capture_repo_totals(
        repo, dataclasses.replace(drifted, total_commit_count=_STRIDE - 1)
    ) == truth


def test_the_stride_is_the_only_thing_that_re_anchors_a_clean_history(tmp_path) -> None:
    """Pins the stride's trigger, so it cannot be tuned away by accident."""
    repo = _init(tmp_path)
    _commit(repo, tmp_path, "a.py", "x = 1\n")
    first = capture_repo_totals(repo)
    _commit(repo, tmp_path, "b.py", "y = 2\n")

    revs: list[str] = []
    capture_repo_totals(_SpyRepo(repo, revs), first)
    assert revs == [f"{first.churn_anchor_sha}..{repo.head.commit.hexsha}"]

    # Same history, but the stored count sits just under a stride boundary.
    revs.clear()
    capture_repo_totals(
        _SpyRepo(repo, revs), dataclasses.replace(first, total_commit_count=_STRIDE - 1)
    )
    assert revs == [repo.head.commit.hexsha]


def test_an_unresolvable_head_never_folds(tmp_path) -> None:
    """A capture that could not resolve HEAD must not fold against the name.

    It stores no anchor, so folding would let the totals advance while the
    anchor stayed where it was — the one direction the write rule cannot catch.
    """
    repo = _init(tmp_path)
    _commit(repo, tmp_path, "a.py", "x = 1\nx = 2\n")
    anchored = capture_repo_totals(repo)

    class _NoHead(_SpyRepo):
        @property
        def head(self):
            raise ValueError("unborn")

    revs: list[str] = []
    totals = capture_repo_totals(_NoHead(repo, revs), anchored)
    assert revs == ["HEAD"]
    assert totals.churn_anchor_sha is None


# ---------------------------------------------------------------------------
# The offset that never converged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("iso", "expected"),
    [
        ("2026-05-29T16:54:14Z", 0),
        ("2026-05-29T17:54:14+02:00", 120),
        ("2026-05-29T11:54:14-05:00", -300),
        ("2026-05-29T16:54:14", None),
        ("", None),
    ],
)
def test_tz_offset_reads_gits_utc_form(iso: str, expected: int | None) -> None:
    """``%cI`` is strict ISO 8601, which spells a zero offset ``Z``.

    Reading that as unparseable left every UTC commit's offset NULL, which is
    the same state as "indexed before the column existed" — so the update-time
    backfill re-selected and re-looked-up the same commits on every run and
    never converged.
    """
    assert _tz_offset_minutes(iso) == expected


def test_a_utc_commit_captures_an_offset(tmp_path) -> None:
    """End to end through the real capture, not just the parser."""
    from repowise.core.ingestion.git_indexer import GitIndexer

    repo = _init(tmp_path)
    env = {"GIT_AUTHOR_DATE": "2026-01-02T03:04:05+0000",
           "GIT_COMMITTER_DATE": "2026-01-02T03:04:05+0000"}
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    repo.index.add(["a.py"])
    subprocess.run(
        ["git", "commit", "-q", "-m", "feat: a"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        check=True,
    )
    sha = repo.head.commit.hexsha

    offsets = GitIndexer(tmp_path).capture_commit_offsets([sha])
    assert offsets == {sha: 0}
