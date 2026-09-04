"""get_change_risk's branch_overlap block: who else is editing these files.

Git alone answers it, so the block appears without an index. It is silent when
no other branch shares a file, every row states its basis in words, and the
caps say by how much they cut.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server.tool_change_risk import (
    _BRANCH_OVERLAP_FILES_LIMIT,
    _BRANCH_OVERLAP_LIMIT,
    _SHED_ORDER,
    _branch_overlap_block,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@e.st", "-c", "user.name=T", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def _repo(tmp_path: Path, *, others: dict[str, dict[str, str]]) -> Path:
    """A repo on ``main`` plus one branch per entry of *others*, then back on a work branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _commit(repo, {"a.py": "a\n", "b.py": "b\n", "c.py": "c\n"}, "base")
    for branch, files in others.items():
        _git(repo, "checkout", "-b", branch, "main")
        _commit(repo, files, f"work on {branch}")
    _git(repo, "checkout", "-b", "work", "main")
    _commit(repo, {"a.py": "a mine\n"}, "our change")
    return repo


def _ctx(repo: Path) -> SimpleNamespace:
    """A context with no session factory: the git-only block."""
    return SimpleNamespace(path=str(repo))


async def test_block_is_silent_with_nothing_changed(tmp_path):
    repo = _repo(tmp_path, others={"other": {"a.py": "a theirs\n"}})
    collector = OmissionCollector("t", repo_root=tmp_path)
    assert await _branch_overlap_block(_ctx(repo), {}, collector) is None


async def test_block_is_silent_when_no_branch_shares_a_file(tmp_path):
    repo = _repo(tmp_path, others={"elsewhere": {"c.py": "c theirs\n"}})
    block = await _branch_overlap_block(
        _ctx(repo), {"a.py": {1}}, OmissionCollector("t", repo_root=tmp_path)
    )
    assert block is None


async def test_block_names_only_the_overlapping_branch_and_states_its_basis(tmp_path):
    repo = _repo(
        tmp_path,
        others={"overlapping": {"a.py": "a theirs\n"}, "elsewhere": {"c.py": "c theirs\n"}},
    )

    block = await _branch_overlap_block(
        _ctx(repo), {"a.py": {1}}, OmissionCollector("t", repo_root=tmp_path)
    )

    assert block is not None
    assert [entry["branch"] for entry in block["branches"]] == ["overlapping"]
    entry = block["branches"][0]
    assert [row["file"] for row in entry["files"]] == ["a.py"]
    # Every row says in words why it is listed, and nothing carries a score.
    assert all(row["basis"] for row in entry["files"])
    assert all("score" not in row for row in entry["files"])
    assert block["base"] == "main"
    assert block["current"] == "work"
    assert block["scanned"] >= 2
    assert "edit files this change also edits" in block["summary"]


async def test_branch_list_is_capped_and_says_by_how_much(tmp_path):
    over = _BRANCH_OVERLAP_LIMIT + 2
    repo = _repo(
        tmp_path,
        others={f"branch{i}": {"a.py": f"a from {i}\n"} for i in range(over)},
    )

    block = await _branch_overlap_block(
        _ctx(repo), {"a.py": {1}}, OmissionCollector("t", repo_root=tmp_path)
    )

    assert block is not None
    assert len(block["branches"]) == _BRANCH_OVERLAP_LIMIT
    assert block["branches_total"] == over
    assert block["branches_truncated"] is True
    assert block["branches_emitted"] == _BRANCH_OVERLAP_LIMIT


async def test_an_uncut_block_carries_no_truncation_fields(tmp_path):
    repo = _repo(tmp_path, others={"overlapping": {"a.py": "a theirs\n"}})
    block = await _branch_overlap_block(
        _ctx(repo), {"a.py": {1}}, OmissionCollector("t", repo_root=tmp_path)
    )
    assert block is not None
    assert "branches_total" not in block
    assert "files_total" not in block["branches"][0]


async def test_the_per_branch_file_list_is_capped(tmp_path):
    over = _BRANCH_OVERLAP_FILES_LIMIT + 3
    shared = {f"src/f{i}.py": f"{i}\n" for i in range(over)}
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _commit(repo, shared, "base")
    _git(repo, "checkout", "-b", "overlapping", "main")
    _commit(repo, {name: "theirs\n" for name in shared}, "their change")
    _git(repo, "checkout", "-b", "work", "main")
    _commit(repo, {name: "mine\n" for name in shared}, "our change")

    block = await _branch_overlap_block(
        _ctx(repo),
        {name: {1} for name in shared},
        OmissionCollector("t", repo_root=tmp_path),
    )

    assert block is not None
    entry = block["branches"][0]
    assert len(entry["files"]) == _BRANCH_OVERLAP_FILES_LIMIT
    assert entry["files_total"] == over
    assert entry["files_truncated"] is True


async def test_a_dropped_branch_reaches_the_store_with_its_files_intact(tmp_path):
    """The branch cap runs first, so a dropped entry is stored whole."""
    over_files = _BRANCH_OVERLAP_FILES_LIMIT + 2
    shared = {f"src/f{i}.py": f"{i}\n" for i in range(over_files)}
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _commit(repo, shared, "base")
    for i in range(_BRANCH_OVERLAP_LIMIT + 1):
        _git(repo, "checkout", "-b", f"branch{i}", "main")
        _commit(repo, {name: f"theirs {i}\n" for name in shared}, f"work {i}")
    _git(repo, "checkout", "-b", "work", "main")
    _commit(repo, {name: "mine\n" for name in shared}, "our change")

    collector = OmissionCollector("get_change_risk", repo_root=tmp_path)
    block = await _branch_overlap_block(
        _ctx(repo), {name: {1} for name in shared}, collector
    )

    assert block is not None
    assert block["branches_truncated"] is True
    payload: dict = {"branch_overlap": block}
    collector.attach(payload)

    stored = _stored_text(tmp_path, payload)
    # The dropped entry keeps every file: it was never cut in place, and the
    # per-entry labels only name indexes the response actually carries.
    assert stored.count(f"src/f{over_files - 1}.py") >= 1
    for entry in block["branches"]:
        assert len(entry["files"]) == _BRANCH_OVERLAP_FILES_LIMIT
        assert entry["files_total"] == over_files


def _stored_text(tmp_path: Path, payload: dict) -> str:
    """Everything this response pushed to the omission store, as one string."""
    from repowise.core.distill.store import OmissionStore, default_store_path

    refs = payload["_meta"]["omitted"]["refs"]
    store = OmissionStore(default_store_path(tmp_path))
    try:
        return "\n".join(store.get(ref) or "" for ref in refs)
    finally:
        store.close()


async def test_a_scan_that_cannot_answer_yields_no_block(tmp_path, monkeypatch):
    """A timeout or a wedged git is silence, never a half-built block."""
    from repowise.server.mcp_server import tool_change_risk as module

    repo = _repo(tmp_path, others={"overlapping": {"a.py": "a theirs\n"}})

    async def _raises(*_args, **_kwargs):
        raise OSError("git is gone")

    monkeypatch.setattr(module, "_scan_overlap", _raises)
    collector = OmissionCollector("t", repo_root=tmp_path)
    assert await _branch_overlap_block(_ctx(repo), {"a.py": {1}}, collector) is None

    async def _never_returns(*_args, **_kwargs):
        raise TimeoutError

    monkeypatch.setattr(module, "_scan_overlap", _never_returns)
    assert await _branch_overlap_block(_ctx(repo), {"a.py": {1}}, collector) is None


def test_branch_overlap_participates_in_the_response_ceiling(tmp_path):
    """A block outside the shed order can never be shed, so it has to be in it."""
    from repowise.server.mcp_server._budget import fit_to_budget

    # Shed after the fix record and before the consumer list: who else is in
    # these files outlives what the diff weighs and is cheaper than what breaks.
    assert _SHED_ORDER.index("prior_fixes") < _SHED_ORDER.index("branch_overlap")
    assert _SHED_ORDER.index("branch_overlap") < _SHED_ORDER.index("cross_repo")
    assert _SHED_ORDER.index("change_shape.independent_changes") < _SHED_ORDER.index("change_shape")

    payload = {
        "score": 7.0,
        "branch_overlap": {"branches": [{"branch": "b", "pad": "x" * 400}] * 200},
        "cross_repo": {"consumers": [{"repo": "frontend"}]},
    }
    collector = OmissionCollector("get_change_risk", repo_root=tmp_path)
    fit_to_budget(payload, _SHED_ORDER, collector)

    assert "branch_overlap" not in payload
    assert payload["truncated"] is True
    assert payload["cross_repo"]["consumers"] == [{"repo": "frontend"}]


async def test_get_change_risk_carries_the_block_end_to_end(tmp_path, monkeypatch):
    from repowise.server.mcp_server import tool_change_risk as module

    repo = _repo(tmp_path, others={"overlapping": {"a.py": "a theirs\n"}})

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    payload = await module.get_change_risk("HEAD", baseline=0)

    assert [e["branch"] for e in payload["branch_overlap"]["branches"]] == ["overlapping"]
    # No index, so the diff is never split into changes the index cannot see.
    assert "independent_changes" not in payload["change_shape"]


async def test_reading_other_branches_does_not_widen_the_response_targets(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.models import Repository
    from repowise.server.mcp_server import tool_change_risk as module

    repo = _repo(tmp_path, others={"overlapping": {"a.py": "a theirs\n"}})
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wiki.db').as_posix()}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Repository(id="repo1", name="repo", local_path=str(repo)))
        await session.commit()

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    seen: dict = {}
    build_meta = module._build_meta

    def _capture(**kwargs):
        seen.update(kwargs)
        return build_meta(**kwargs)

    monkeypatch.setattr(module, "_build_meta", _capture)
    payload = await module.get_change_risk("HEAD", baseline=0)

    assert payload["branch_overlap"]["branches"][0]["branch"] == "overlapping"
    # The block reads files on other branches; what the response is about, and
    # what its freshness is scoped to, is still only the files this change edits.
    assert seen["targets"] == ["a.py"]


async def test_targets_are_the_changed_files_without_an_index(tmp_path, monkeypatch):
    """The block needs only git, so an unindexed call now scopes freshness too."""
    from repowise.server.mcp_server import tool_change_risk as module

    repo = _repo(tmp_path, others={})

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    seen: dict = {}
    build_meta = module._build_meta

    def _capture(**kwargs):
        seen.update(kwargs)
        return build_meta(**kwargs)

    monkeypatch.setattr(module, "_build_meta", _capture)
    await module.get_change_risk("HEAD", baseline=0)

    assert seen["targets"] == ["a.py"]


async def test_get_change_risk_omits_the_block_without_another_branch(tmp_path, monkeypatch):
    from repowise.server.mcp_server import tool_change_risk as module

    repo = _repo(tmp_path, others={})

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    payload = await module.get_change_risk("HEAD", baseline=0)

    assert "branch_overlap" not in payload
