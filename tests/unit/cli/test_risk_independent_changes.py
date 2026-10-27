"""``repowise risk REVSPEC`` reporting how a diff splits into separate changes."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.commands.risk_cmd import risk_command
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GraphEdge, GraphNode, Repository, _new_uuid

#: The last one is a markdown page: a node the graph never links, so it is
#: reported beside the groups rather than grouped alone.
_FILES = ("a.py", "b.py", "x.py", "y.py", "notes.md")


@pytest.fixture(autouse=True)
def _repo_local_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command must open ``<repo>/.repowise/wiki.db``, not an inherited URL."""
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo_with_one_commit(tmp_path: Path, name: str, files: tuple[str, ...] = _FILES) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(["init", "-q"], repo)
    (repo / "README.md").write_text("# seed\n", encoding="utf-8")
    _git(["add", "README.md"], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-m", "seed"], repo)
    for relative_path in files:
        (repo / relative_path).write_text("value = 1\n", encoding="utf-8")
        _git(["add", relative_path], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-m", "work"], repo)
    return repo


async def _write_index(
    repo: Path, edges: tuple[tuple[str, str], ...], files: tuple[str, ...] = _FILES
) -> None:
    """Seed the repo-local store with a file node each and the given import edges."""
    db_path = repo / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await init_db(engine)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(Repository(id="repo1", name="repo", local_path=str(repo.resolve())))
        for relative_path in files:
            session.add(
                GraphNode(
                    id=_new_uuid(),
                    repository_id="repo1",
                    node_id=relative_path,
                    node_type="file",
                )
            )
        for source, target in edges:
            session.add(
                GraphEdge(
                    id=_new_uuid(),
                    repository_id="repo1",
                    source_node_id=source,
                    target_node_id=target,
                    edge_type="imports",
                )
            )
        await session.commit()
    await engine.dispose()


def _run(repo: Path, fmt: str, revspec: str = "HEAD", path: Path | None = None):
    return CliRunner().invoke(
        risk_command,
        [revspec, "--path", str(path or repo), "--baseline", "0", "--format", fmt],
    )


def test_two_unconnected_groups_are_named_in_the_table(tmp_path: Path) -> None:
    repo = _repo_with_one_commit(tmp_path, "split")
    asyncio.run(_write_index(repo, (("a.py", "b.py"), ("x.py", "y.py"))))

    result = _run(repo, "table")

    assert result.exit_code == 0, result.output
    assert "This diff is 2 independent changes" in result.output
    assert "1. 2 files: a.py, b.py" in result.output
    assert "2. 2 files: x.py, y.py" in result.output
    assert "Left out of the grouping: notes.md" in result.output
    assert "Basis:" in result.output


def test_json_carries_the_same_split(tmp_path: Path) -> None:
    repo = _repo_with_one_commit(tmp_path, "split-json")
    asyncio.run(_write_index(repo, (("a.py", "b.py"), ("x.py", "y.py"))))

    result = _run(repo, "json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    block = payload["independent_changes"]
    assert block["count"] == 2
    assert [group["files"] for group in block["groups"]] == [["a.py", "b.py"], ["x.py", "y.py"]]
    assert block["ungrouped_files"] == ["notes.md"]


def test_a_connected_diff_prints_nothing_extra(tmp_path: Path) -> None:
    repo = _repo_with_one_commit(tmp_path, "connected")
    asyncio.run(_write_index(repo, (("a.py", "b.py"), ("b.py", "x.py"), ("x.py", "y.py"))))

    result = _run(repo, "table")

    assert result.exit_code == 0, result.output
    assert "independent changes" not in result.output


def test_an_unindexed_repo_is_scored_without_the_section(tmp_path: Path) -> None:
    repo = _repo_with_one_commit(tmp_path, "bare")

    table = _run(repo, "table")
    assert table.exit_code == 0, table.output
    assert "independent changes" not in table.output

    machine = _run(repo, "json")
    assert machine.exit_code == 0, machine.output
    assert "independent_changes" not in json.loads(machine.output)


def test_a_long_ungrouped_list_is_cut(tmp_path: Path) -> None:
    pages = tuple(f"d{index:02d}.md" for index in range(12))
    repo = _repo_with_one_commit(tmp_path, "many-pages", ("a.py", "b.py", "x.py", "y.py", *pages))
    asyncio.run(
        _write_index(
            repo, (("a.py", "b.py"), ("x.py", "y.py")), ("a.py", "b.py", "x.py", "y.py", *pages)
        )
    )

    result = _run(repo, "table")

    assert result.exit_code == 0, result.output
    line = next(t for t in result.output.splitlines() if t.startswith("  Left out of the grouping"))
    assert line.endswith("and 2 more")


_RANGE_FILES = ("a.py", "b.py", "x.py", "y.py")
_RANGE_EDGES = (("a.py", "b.py"), ("x.py", "y.py"))


def _commit(repo: Path, paths: tuple[str, ...], message: str) -> None:
    for relative_path in paths:
        (repo / relative_path).write_text(f"value = 1  # {message}\n", encoding="utf-8")
        _git(["add", relative_path], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-m", message], repo)


def _repo_with_two_changes(tmp_path: Path, name: str, *, shared: bool) -> Path:
    """Three commits: a seed, then two changes the index links to nothing shared."""
    repo = tmp_path / name
    repo.mkdir()
    _git(["init", "-q"], repo)
    (repo / "README.md").write_text("# seed\n", encoding="utf-8")
    _git(["add", "README.md"], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-m", "seed"], repo)
    _commit(repo, ("a.py", "b.py"), "first")
    _commit(repo, ("x.py", "y.py", *(("a.py",) if shared else ())), "second")
    return repo


def test_a_commit_touching_both_sides_makes_them_one_change(tmp_path: Path) -> None:
    joined = _repo_with_two_changes(tmp_path, "joined", shared=True)
    asyncio.run(_write_index(joined, _RANGE_EDGES, _RANGE_FILES))
    split = _repo_with_two_changes(tmp_path, "split-range", shared=False)
    asyncio.run(_write_index(split, _RANGE_EDGES, _RANGE_FILES))

    together = _run(joined, "table", "HEAD~2..HEAD")
    apart = _run(split, "table", "HEAD~2..HEAD")

    assert together.exit_code == 0, together.output
    assert "independent changes" not in together.output
    assert apart.exit_code == 0, apart.output
    assert "This diff is 2 independent changes" in apart.output


def test_a_subdirectory_path_still_reports_the_split(tmp_path: Path) -> None:
    repo = _repo_with_one_commit(tmp_path, "from-subdir")
    asyncio.run(_write_index(repo, _RANGE_EDGES))
    package = repo / "pkg"
    package.mkdir()

    result = _run(repo, "table", path=package)

    assert result.exit_code == 0, result.output
    assert "This diff is 2 independent changes" in result.output


def test_an_index_the_query_cannot_read_drops_the_section(tmp_path, monkeypatch) -> None:
    """An index written by an older version can fail the query, not the command."""
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.analysis import independent_changes as module

    repo = _repo_with_one_commit(tmp_path, "old-schema")
    asyncio.run(_write_index(repo, _RANGE_EDGES))

    async def _fail(*args, **kwargs):
        raise SQLAlchemyError("old schema")

    monkeypatch.setattr(module, "independent_changes", _fail)
    result = _run(repo, "table")

    assert result.exit_code == 0, result.output
    assert "independent changes" not in result.output
