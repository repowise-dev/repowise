"""Which other branches edit the files this change edits.

The git half runs against a real repository built under ``tmp_path``: branch
listing, three-dot diffs and ahead/behind are the whole question, so a fake
would only assert that the fake agrees with itself. The index half seeds a real
wiki.db for the same reason.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.core.analysis.branch_overlap import rank_with_index, scan_branches
from repowise.core.git_refs import ahead_behind_many, files_by_ref
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    GitMetadata,
    GraphNode,
    Repository,
    _new_uuid,
)

_REPO_ID = "repo1"

# Our changed files, as a caller would hand them over: two real files plus a
# lockfile the noise filter must drop.
_OURS = ["hub.py", "shared.py", "package-lock.json", "go.mod"]


def _overlap(repo, ours=None, **kwargs):
    """The git-only answer, which is what ``scan_branches`` wraps."""
    return scan_branches(str(repo), _OURS if ours is None else ours, base="main", **kwargs).overlap


def _run(repo, *args, when: str | None = None) -> str:
    env = None
    if when is not None:
        env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    result = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return result.stdout.strip()


def _commit(repo, message: str, files: dict[str, str], when: str) -> None:
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-m", message, when=when)


@pytest.fixture
def repo(tmp_path):
    """A repository whose branches cover every outcome the scan can produce."""
    path = tmp_path / "repo"
    path.mkdir()
    _run(path, "init", "-b", "main")
    _commit(
        path,
        "base",
        {
            "shared.py": "0\n",
            "hub.py": "0\n",
            "other.py": "0\n",
            "partner.py": "0\n",
            "zero.py": "0\n",
            "p3.py": "0\n",
            "go.mod": "0\n",
            "p4.py": "0\n",
            "package-lock.json": "{}\n",
        },
        "2026-01-01T00:00:00+00:00",
    )

    # Oldest branch, and a hit: it decides which branch the limit leaves out.
    _run(path, "checkout", "-b", "oldest")
    _commit(path, "oldest", {"shared.py": "oldest\n"}, "2026-01-02T00:00:00+00:00")

    _run(path, "checkout", "main")
    _run(path, "checkout", "-b", "elsewhere")
    _commit(path, "elsewhere", {"other.py": "elsewhere\n"}, "2026-01-03T00:00:00+00:00")

    _run(path, "checkout", "main")
    _run(path, "checkout", "-b", "bump")
    _commit(path, "bump", {"go.mod": "bumped' + NL + '"}, "2026-01-03T12:00:00+00:00")

    _run(path, "checkout", "main")
    _run(path, "checkout", "-b", "noisy")
    _commit(path, "noisy", {"package-lock.json": '{"a": 1}\n'}, "2026-01-04T00:00:00+00:00")

    _run(path, "checkout", "main")
    _run(path, "checkout", "-b", "already-merged")
    _commit(path, "merged", {"shared.py": "merged\n"}, "2026-01-05T00:00:00+00:00")
    _run(path, "checkout", "main")
    _run(path, "merge", "--no-ff", "-m", "merge", "already-merged", when="2026-01-06T00:00:00+00:00")

    _run(path, "checkout", "-b", "hitter")
    _commit(
        path,
        "hitter",
        {
            "shared.py": "hitter\n",
            "hub.py": "hitter\n",
            "partner.py": "1\n",
            "zero.py": "1\n",
            "p3.py": "1\n",
            "p4.py": "1\n",
            "package-lock.json": '{"b": 2}\n',
            "go.mod": "hitter\n",
        },
        "2026-01-07T00:00:00+00:00",
    )
    # A remote-tracking twin of the same tip: one entry, under the local name.
    _run(path, "update-ref", "refs/remotes/origin/hitter", _run(path, "rev-parse", "hitter"))

    # A commit on main after the branch point, so the branch is behind as well as ahead.
    _run(path, "checkout", "main")
    _commit(path, "moved on", {"other.py": "moved\n"}, "2026-01-08T00:00:00+00:00")

    _run(path, "checkout", "-b", "feat/x")
    _commit(path, "ours", {"shared.py": "ours\n", "hub.py": "ours\n"}, "2026-01-09T00:00:00+00:00")

    # Stacked on top of this change: the same line of work, not a parallel edit.
    _run(path, "checkout", "-b", "stacked")
    _commit(path, "stacked", {"shared.py": "stacked\n"}, "2026-01-10T00:00:00+00:00")
    _run(path, "checkout", "feat/x")
    return path


# ---------------------------------------------------------------------------
# Git


def test_only_branches_sharing_a_file_appear(repo):
    result = _overlap(repo)

    assert [entry.branch for entry in result.branches] == ["hitter", "oldest"]
    assert result.current == "feat/x"
    assert result.base == "main"
    assert [row.file for row in result.branches[1].rows] == ["shared.py"]


def test_scanned_and_total_count_every_candidate(repo):
    result = _overlap(repo)

    # oldest, elsewhere, bump, noisy, hitter: the remote twin collapsed into "hitter".
    assert (result.scanned, result.total) == (5, 5)
    assert result.to_dict()["truncated"] is False


def test_the_limit_leaves_out_the_oldest_branch(repo):
    result = _overlap(repo, limit=4)

    assert (result.scanned, result.total) == (4, 5)
    assert [entry.branch for entry in result.branches] == ["hitter"]
    assert result.to_dict()["truncated"] is True


def test_a_branch_this_change_is_stacked_on_is_not_listed(repo):
    """A parent edits our files by construction: one change, two refs."""
    names = [entry.branch for entry in _overlap(repo).branches]

    assert "already-merged" not in names


def test_a_branch_stacked_on_this_change_is_not_listed(repo):
    """A branch containing HEAD shares every file, and is not a parallel edit."""
    result = _overlap(repo)

    assert "stacked" not in [entry.branch for entry in result.branches]
    # Neither stacked ref counts as a candidate that was passed over.
    assert result.total == 5


def test_a_branch_touching_other_files_is_absent(repo):
    names = [entry.branch for entry in _overlap(repo).branches]

    assert "elsewhere" not in names


def test_a_shared_dependency_manifest_is_not_an_overlap(repo):
    """Every bump edits the manifest, so sharing it is not shared work."""
    names = [entry.branch for entry in _overlap(repo).branches]

    assert "bump" not in names


def test_a_shared_lockfile_is_not_an_overlap(repo):
    names = [entry.branch for entry in _overlap(repo).branches]

    assert "noisy" not in names


def test_the_remote_twin_collapses_into_the_local_name(repo):
    names = [entry.branch for entry in _overlap(repo).branches]

    assert names.count("hitter") == 1
    assert "origin/hitter" not in names


def test_the_base_and_the_current_branch_are_skipped(repo):
    names = [entry.branch for entry in _overlap(repo).branches]

    assert "main" not in names
    assert "feat/x" not in names


def test_ahead_is_the_branch_and_behind_is_the_base(repo):
    entry = _overlap(repo).branches[0]

    assert entry.branch == "hitter"
    assert (entry.ahead, entry.behind) == (1, 1)


def test_git_alone_lists_shared_files_alphabetically(repo):
    entry = _overlap(repo).branches[0]

    assert [(row.file, row.basis) for row in entry.rows] == [
        ("hub.py", "same file"),
        ("shared.py", "same file"),
    ]


def test_nothing_shared_gives_the_empty_summary(repo):
    result = _overlap(repo, ["untouched.py"])

    assert result.branches == ()
    assert result.to_dict()["summary"] == (
        "No other open branch edits a file this change edits (5 scanned of 5)."
    )


def test_the_summary_counts_the_branches_that_overlap(repo):
    assert _overlap(repo).to_dict()["summary"] == (
        "2 of 5 open branches (5 exist) edit files this change also edits."
    )


# ---------------------------------------------------------------------------
# The bulk read that decides which branches earn an exact diff


def test_one_read_names_every_file_a_ref_changed(repo):
    assert files_by_ref(str(repo), "main", ["stacked"]) == {
        "stacked": frozenset({"shared.py", "hub.py"})
    }


def test_a_ref_with_no_commits_of_its_own_is_absent(repo):
    assert files_by_ref(str(repo), "main", ["already-merged"]) == {}


def test_one_read_counts_ahead_and_behind_for_every_ref(repo):
    """Ahead is the branch's own commits, behind is what the base gained since."""
    assert ahead_behind_many(str(repo), "main", ["refs/heads/hitter", "refs/heads/oldest"]) == {
        "hitter": (1, 1),
        "oldest": (1, 3),
    }


def test_a_shared_commit_is_credited_to_one_ref(repo):
    result = files_by_ref(str(repo), "main", ["stacked", "feat/x"])

    # "stacked" sits on "feat/x", so the commit they share counts once.
    assert result["feat/x"] == frozenset({"shared.py", "hub.py"})
    assert result["stacked"] == frozenset({"shared.py"})


# ---------------------------------------------------------------------------
# Index


async def _seed(tmp_path, *, partners: list[dict]) -> async_sessionmaker:
    """A wiki.db where shared.py outranks hub.py and carries the given partners."""
    db_path = tmp_path / "wiki.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Repository(id=_REPO_ID, name="repo", local_path=str(tmp_path)))
        # hub.py has the higher pagerank; shared.py still wins on temporal weight.
        for path, rank in (("shared.py", 0.2), ("hub.py", 0.3), ("partner.py", 0.1)):
            session.add(
                GraphNode(
                    id=_new_uuid(),
                    repository_id=_REPO_ID,
                    node_id=path,
                    node_type="file",
                    pagerank=rank,
                )
            )
        session.add(
            GitMetadata(
                id=_new_uuid(),
                repository_id=_REPO_ID,
                file_path="shared.py",
                temporal_hotspot_score=2.0,
                co_change_partners_json=json.dumps(partners),
            )
        )
        session.add(
            GitMetadata(
                id=_new_uuid(),
                repository_id=_REPO_ID,
                file_path="hub.py",
                temporal_hotspot_score=0.0,
            )
        )
        await session.commit()
    return factory


def _partner(path: str, *, frequency: int, self_commits: int = 9) -> dict:
    return {
        "file_path": path,
        "co_change_count": 5.0,
        "frequency": frequency,
        "self_commits": self_commits,
    }


async def _hitter(tmp_path, repo, partners: list[dict]):
    factory = await _seed(tmp_path, partners=partners)
    scan = scan_branches(str(repo), _OURS, base="main")
    async with factory() as session:
        result = await rank_with_index(session, _REPO_ID, scan)
    return next(entry for entry in result.branches if entry.branch == "hitter"), result


async def test_shared_files_are_ordered_by_the_hub_metric(tmp_path, repo):
    entry, _ = await _hitter(tmp_path, repo, [])

    # Alphabetical order would put hub.py first.
    assert [row.file for row in entry.rows] == ["shared.py", "hub.py"]


async def test_a_co_change_row_follows_the_shared_files(tmp_path, repo):
    entry, _ = await _hitter(tmp_path, repo, [_partner("partner.py", frequency=6)])

    assert [row.file for row in entry.rows] == ["shared.py", "hub.py", "partner.py"]
    row = entry.rows[-1]
    assert row.basis == "co-change pair, 6 of 9 commits"
    assert row.partner == "shared.py"


async def test_a_partner_without_a_count_states_no_basis(tmp_path, repo):
    entry, _ = await _hitter(tmp_path, repo, [_partner("zero.py", frequency=0)])

    assert [row.file for row in entry.rows] == ["shared.py", "hub.py"]


async def test_a_partner_that_is_already_shared_is_not_repeated(tmp_path, repo):
    entry, _ = await _hitter(tmp_path, repo, [_partner("hub.py", frequency=7)])

    assert [row.file for row in entry.rows] == ["shared.py", "hub.py"]
    assert all(row.basis == "same file" for row in entry.rows)


async def test_history_alone_never_puts_a_branch_on_the_list(tmp_path, repo):
    _, result = await _hitter(tmp_path, repo, [_partner("other.py", frequency=6)])

    # "elsewhere" edits other.py and nothing else: a co-change pair is not a hit.
    assert "elsewhere" not in [entry.branch for entry in result.branches]


async def test_a_pair_that_mostly_does_not_hold_makes_no_row(tmp_path, repo):
    entry, _ = await _hitter(tmp_path, repo, [_partner("partner.py", frequency=12, self_commits=43)])

    assert [row.file for row in entry.rows] == ["shared.py", "hub.py"]


async def test_a_pair_that_holds_half_the_time_makes_a_row(tmp_path, repo):
    entry, _ = await _hitter(tmp_path, repo, [_partner("partner.py", frequency=14, self_commits=21)])

    assert entry.rows[-1].basis == "co-change pair, 14 of 21 commits"


async def test_history_rows_are_capped_per_branch(tmp_path, repo):
    entry, _ = await _hitter(
        tmp_path,
        repo,
        [
            _partner("partner.py", frequency=8),
            _partner("zero.py", frequency=7),
            _partner("p3.py", frequency=6),
            _partner("p4.py", frequency=5),
        ],
    )

    assert [row.file for row in entry.rows] == [
        "shared.py",
        "hub.py",
        "partner.py",
        "zero.py",
        "p3.py",
    ]


async def test_a_scan_that_found_nothing_is_not_ranked(tmp_path, repo):
    """Nothing shared means no index read, and the git answer is returned as it is."""
    factory = await _seed(tmp_path, partners=[])
    scan = scan_branches(str(repo), ["untouched.py"], base="main")
    async with factory() as session:
        assert await rank_with_index(session, _REPO_ID, scan) is scan.overlap


async def test_a_shared_lockfile_or_manifest_never_becomes_a_row(tmp_path, repo):
    """The hit branch also edits both, and neither reaches the rows."""
    entry, _ = await _hitter(tmp_path, repo, [])

    assert [row.file for row in entry.rows] == ["shared.py", "hub.py"]


async def test_a_pair_at_exactly_half_makes_a_row(tmp_path, repo):
    entry, _ = await _hitter(tmp_path, repo, [_partner("partner.py", frequency=12, self_commits=24)])

    assert entry.rows[-1].basis == "co-change pair, 12 of 24 commits"


async def test_a_pair_just_under_half_makes_no_row(tmp_path, repo):
    entry, _ = await _hitter(tmp_path, repo, [_partner("partner.py", frequency=12, self_commits=25)])

    assert [row.file for row in entry.rows] == ["shared.py", "hub.py"]


async def test_the_wire_shape_names_every_field(tmp_path, repo):
    _, result = await _hitter(tmp_path, repo, [_partner("partner.py", frequency=6)])
    payload = result.to_dict()

    assert set(payload) == {
        "base",
        "current",
        "branches",
        "scanned",
        "total",
        "truncated",
        "summary",
    }
    branch = payload["branches"][0]
    assert set(branch) == {"branch", "ahead", "behind", "last_commit", "files"}
    assert branch["last_commit"] == "2026-01-07"
    assert branch["files"] == [
        {"file": "shared.py", "basis": "same file"},
        {"file": "hub.py", "basis": "same file"},
        {"file": "partner.py", "partner": "shared.py", "basis": "co-change pair, 6 of 9 commits"},
    ]
