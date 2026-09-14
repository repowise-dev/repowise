"""get_change_risk's change_shape.independent_changes block.

The block says a diff is several changes the index does not connect. It is
silent without an index, for a single changed file, and whenever the changed
files hang together, so an ordinary change grows no new block.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GraphEdge, GraphNode, Repository, _new_uuid
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server.tool_change_risk import (
    _UNGROUPED_FILES_LIMIT,
    _change_shape,
    _independent_changes_block,
)

_REPO_ID = "repo1"


def _collector(tmp_path) -> OmissionCollector:
    return OmissionCollector("get_change_risk", repo_root=tmp_path)


async def _ctx(tmp_path, files: list[str], edges: list[tuple[str, str]]) -> SimpleNamespace:
    """A context over a real wiki.db holding *files* as file nodes and *edges* between them."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "wiki.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Repository(id=_REPO_ID, name="repo", local_path=str(tmp_path)))
        for path in files:
            session.add(
                GraphNode(
                    id=_new_uuid(),
                    repository_id=_REPO_ID,
                    node_id=path,
                    node_type="file",
                    name=path,
                    file_path=path,
                    language="python",
                )
            )
        for source, target in edges:
            session.add(
                GraphEdge(
                    id=_new_uuid(),
                    repository_id=_REPO_ID,
                    source_node_id=source,
                    target_node_id=target,
                    edge_type="imports",
                )
            )
        await session.commit()
    return SimpleNamespace(path=str(tmp_path), session_factory=factory)


async def test_block_is_silent_without_an_index(tmp_path):
    ctx = SimpleNamespace(path="/repo")
    changed = {"a.py": {1}, "b.py": {2}}
    assert await _independent_changes_block(ctx, changed, _collector(tmp_path)) is None


async def test_block_is_silent_for_fewer_than_two_changed_files(tmp_path):
    ctx = SimpleNamespace(path="/repo", session_factory=object())
    assert await _independent_changes_block(ctx, {}, _collector(tmp_path)) is None
    assert await _independent_changes_block(ctx, {"a.py": {1}}, _collector(tmp_path)) is None


async def test_block_is_silent_when_the_index_connects_the_changed_files(tmp_path):
    ctx = await _ctx(tmp_path, ["a.py", "b.py"], [("a.py", "b.py")])
    changed = {"a.py": {1}, "b.py": {2}}
    assert await _independent_changes_block(ctx, changed, _collector(tmp_path)) is None


async def test_block_reports_the_groups_and_states_its_basis(tmp_path):
    """Two connected pairs with nothing between them are two changes, not one."""
    ctx = await _ctx(
        tmp_path,
        ["a.py", "b.py", "c.py", "d.py"],
        [("a.py", "b.py"), ("c.py", "d.py")],
    )

    block = await _independent_changes_block(
        ctx, {"a.py": {1}, "b.py": {1}, "c.py": {1}, "d.py": {1}}, _collector(tmp_path)
    )

    assert block is not None
    assert block["count"] == 2
    assert sorted(g["files"] for g in block["groups"]) == [["a.py", "b.py"], ["c.py", "d.py"]]
    # The claim is about the index, and the block says so rather than implying
    # the code itself has no connection.
    assert "index" in block["basis"]
    assert "independent changes" in block["summary"]
    # No score, percentage or adjective anywhere on the wire.
    assert "score" not in block


async def test_a_file_the_index_does_not_hold_is_never_grouped(tmp_path):
    ctx = await _ctx(
        tmp_path,
        ["a.py", "b.py", "c.py", "d.py"],
        [("a.py", "b.py"), ("c.py", "d.py")],
    )

    block = await _independent_changes_block(
        ctx,
        {"a.py": {1}, "b.py": {1}, "c.py": {1}, "d.py": {1}, "new.py": {1}},
        _collector(tmp_path),
    )

    assert block is not None
    assert block["ungrouped_files"] == ["new.py"]
    assert all("new.py" not in g["files"] for g in block["groups"])


async def test_the_ungrouped_file_list_is_capped_and_says_by_how_much(tmp_path):
    """A docs-heavy release commit can leave hundreds of unlinkable files."""
    over = _UNGROUPED_FILES_LIMIT + 3
    loose = [f"docs/page{i}.md" for i in range(over)]
    ctx = await _ctx(
        tmp_path,
        ["a.py", "b.py", "c.py", "d.py"],
        [("a.py", "b.py"), ("c.py", "d.py")],
    )

    changed = {p: {1} for p in ["a.py", "b.py", "c.py", "d.py", *loose]}
    block = await _independent_changes_block(ctx, changed, _collector(tmp_path))

    assert block is not None
    assert len(block["ungrouped_files"]) == _UNGROUPED_FILES_LIMIT
    assert block["ungrouped_files_total"] == over
    assert block["ungrouped_files_truncated"] is True


async def test_a_range_hands_the_core_what_each_commit_touched(tmp_path, monkeypatch):
    """Which files moved together across the range is the strongest link there is."""
    import subprocess

    from repowise.core.analysis import independent_changes as core

    repo = tmp_path / "repo"
    repo.mkdir()

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@e.st", "-c", "user.name=T", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    _git("init", "-b", "main")
    for i, files in enumerate([["a.py", "b.py"], ["c.py"]]):
        for name in files:
            (repo / name).write_text(f"{name} {i}\n", encoding="utf-8")
        _git("add", "-A")
        _git("commit", "-m", f"c{i}")

    ctx = await _ctx(tmp_path / "index", ["a.py", "b.py", "c.py"], [])
    ctx.path = str(repo)
    seen: dict = {}

    async def _capture(session, repo_id, changed_files, *, commit_sets=()):
        seen["commit_sets"] = [set(s) for s in commit_sets]
        return None

    monkeypatch.setattr(core, "independent_changes", _capture)
    await _independent_changes_block(
        ctx, {"a.py": {1}, "b.py": {1}, "c.py": {1}}, _collector(tmp_path), "HEAD~1..HEAD"
    )

    assert seen["commit_sets"] == [{"c.py"}]


async def test_get_change_risk_carries_the_split_end_to_end(tmp_path, monkeypatch):
    """Two changed files the index links to nothing become two changes."""
    import subprocess

    from repowise.server.mcp_server import tool_change_risk as module

    repo = tmp_path / "repo"
    repo.mkdir()

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@e.st", "-c", "user.name=T", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    _git("init", "-b", "main")
    for name in ("a.py", "b.py"):
        (repo / name).write_text("start" + chr(10), encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-m", "base")
    for name in ("a.py", "b.py"):
        (repo / name).write_text("changed" + chr(10), encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-m", "two unrelated edits")

    # Each changed file is linked to a file outside the diff and to nothing
    # inside it, which is what makes them two changes rather than two unknowns.
    ctx = await _ctx(
        tmp_path / "index",
        ["a.py", "b.py", "lib/one.py", "lib/two.py"],
        [("a.py", "lib/one.py"), ("b.py", "lib/two.py")],
    )

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=ctx.session_factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    payload = await module.get_change_risk("HEAD", baseline=0)

    split = payload["change_shape"]["independent_changes"]
    assert split["count"] == 2
    assert sorted(g["files"] for g in split["groups"]) == [["a.py"], ["b.py"]]


def test_change_shape_carries_the_block_only_when_there_is_one():
    payload = {"score": 3.0, "classification": "moderate"}
    assert "independent_changes" not in _change_shape(payload, {})
    shape = _change_shape(payload, {}, {"count": 2})
    assert shape["independent_changes"] == {"count": 2}
