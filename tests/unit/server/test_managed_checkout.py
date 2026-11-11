"""A checkout the server owns is fetched and fast-forwarded before each sync.

Every test runs real git against a bare repository standing in for GitLab: a
seed clone pushes to it, and the server's checkout is another clone of it.
The end-to-end tests run the real index pipeline, so "the index reaches the
new HEAD" means the pushed file is in the graph and ``state.json`` records it.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from repowise.core.persistence.crud import get_generation_job, upsert_generation_job
from repowise.core.persistence.models import GenerationJob, GraphNode, Repository
from repowise.core.repo_config import save_repo_config
from repowise.server.job_executor import execute_job
from repowise.server.managed_checkout import (
    ManagedCheckoutError,
    fast_forward,
    is_managed_checkout,
    remote_head,
)
from repowise.server.scheduler import setup_scheduler

URL = "https://gitlab.example.com/group/project"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _commit(seed: Path, name: str, body: str) -> str:
    (seed / name).write_text(body, encoding="utf-8")
    _git(seed, "add", name)
    _git(seed, "commit", "-q", "-m", f"add {name}")
    _git(seed, "push", "-q", "origin", "main")
    return _git(seed, "rev-parse", "HEAD")


@pytest.fixture
def remote(tmp_path: Path) -> SimpleNamespace:
    """A bare remote with one commit, and the seed clone that pushes to it."""
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(bare), str(seed))
    _git(seed, "symbolic-ref", "HEAD", "refs/heads/main")
    first = _commit(seed, "a.py", "def a():\n    return 1\n")
    return SimpleNamespace(bare=bare, seed=seed, first=first, root=tmp_path)


def _checkout(remote: SimpleNamespace, *, managed: bool) -> Path:
    path = remote.root / "checkout"
    _git(remote.root, "clone", "-q", str(remote.bare), str(path))
    if managed:
        save_repo_config(path, {"managed_checkout": True})
    return path


def _state_commit(path: Path) -> str | None:
    state = path / ".repowise" / "state.json"
    if not state.is_file():
        return None
    return json.loads(state.read_text(encoding="utf-8")).get("last_sync_commit")


def _tracking_ref(path: Path) -> str:
    """``origin/main`` in the checkout: a fetch is the only thing that moves it."""
    return _git(path, "rev-parse", "refs/remotes/origin/main")


async def _register(session_factory, path: Path) -> str:
    async with session_factory() as session:
        repo = Repository(name="project", url=URL, local_path=str(path), default_branch="main")
        session.add(repo)
        await session.commit()
        return repo.id


async def _run_job(session_factory, app_state, repo_id: str, mode: str):
    async with session_factory() as session:
        job = await upsert_generation_job(session, repository_id=repo_id, config={"mode": mode})
        await session.commit()
        job_id = job.id
    await execute_job(job_id, app_state)
    async with session_factory() as session:
        return await get_generation_job(session, job_id)


async def _indexed_files(session_factory, repo_id: str) -> set[str]:
    async with session_factory() as session:
        rows = await session.execute(
            select(GraphNode.node_id).where(
                GraphNode.repository_id == repo_id, GraphNode.node_type == "file"
            )
        )
        return set(rows.scalars())


async def _drain(app_state) -> None:
    while app_state.background_tasks:
        await asyncio.gather(*list(app_state.background_tasks))


def _push_hook(after: str) -> dict:
    return {
        "ref": "refs/heads/main",
        "before": "0" * 40,
        "after": after,
        "project": {"web_url": URL},
    }


@pytest.fixture
def no_provider(monkeypatch):
    monkeypatch.delenv("REPOWISE_GITLAB_WEBHOOK_TOKEN", raising=False)
    with patch("repowise.server.provider_config.get_chat_provider_instance", return_value=None):
        yield


# ---------------------------------------------------------------------------
# End to end: webhook and poller
# ---------------------------------------------------------------------------


async def test_push_webhook_brings_a_managed_checkout_and_its_index_to_the_pushed_commit(
    client, app, session_factory, remote, no_provider
) -> None:
    checkout = _checkout(remote, managed=True)
    repo_id = await _register(session_factory, checkout)
    job = await _run_job(session_factory, app.state, repo_id, "index_only")
    assert job.status == "completed", job.error_message
    assert _state_commit(checkout) == remote.first

    pushed = _commit(remote.seed, "b.py", "def b():\n    return 2\n")
    response = await client.post(
        "/api/webhooks/gitlab",
        content=json.dumps(_push_hook(pushed)),
        headers={"X-Gitlab-Event": "Push Hook", "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    await _drain(app.state)

    assert _git(checkout, "rev-parse", "HEAD") == pushed
    assert _state_commit(checkout) == pushed
    assert {"a.py", "b.py"} <= await _indexed_files(session_factory, repo_id)


async def test_poller_finds_a_push_to_a_managed_checkout_and_syncs_it(
    app, session_factory, remote, no_provider
) -> None:
    checkout = _checkout(remote, managed=True)
    repo_id = await _register(session_factory, checkout)
    job = await _run_job(session_factory, app.state, repo_id, "index_only")
    assert job.status == "completed", job.error_message

    poll = next(
        j for j in setup_scheduler(session_factory, app_state=app.state).get_jobs()
        if j.id == "polling_fallback"
    )
    # Nothing pushed: the remote head is the indexed commit, so no job.
    await poll.func()
    async with session_factory() as session:
        assert len((await session.execute(select(GenerationJob))).scalars().all()) == 1

    pushed = _commit(remote.seed, "c.py", "def c():\n    return 3\n")
    await poll.func()
    await _drain(app.state)

    assert _git(checkout, "rev-parse", "HEAD") == pushed
    assert _state_commit(checkout) == pushed
    assert "c.py" in await _indexed_files(session_factory, repo_id)


async def test_a_working_tree_is_never_fetched_or_moved(
    client, app, session_factory, remote, no_provider
) -> None:
    """The default: a push reaches neither the checkout nor its index."""
    checkout = _checkout(remote, managed=False)
    repo_id = await _register(session_factory, checkout)
    job = await _run_job(session_factory, app.state, repo_id, "index_only")
    assert job.status == "completed", job.error_message
    tracking = _tracking_ref(checkout)

    pushed = _commit(remote.seed, "b.py", "def b():\n    return 2\n")
    poll = next(
        j for j in setup_scheduler(session_factory, app_state=app.state).get_jobs()
        if j.id == "polling_fallback"
    )
    await poll.func()
    response = await client.post(
        "/api/webhooks/gitlab",
        content=json.dumps(_push_hook(pushed)),
        headers={"X-Gitlab-Event": "Push Hook", "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    await _drain(app.state)

    # The webhook's sync ran on the tree as it stands; nothing was fetched.
    assert _tracking_ref(checkout) == tracking
    assert _git(checkout, "rev-parse", "HEAD") == remote.first
    assert _state_commit(checkout) == remote.first
    assert "b.py" not in await _indexed_files(session_factory, repo_id)
    async with session_factory() as session:
        jobs = (await session.execute(select(GenerationJob))).scalars().all()
    # The first index and the webhook's sync; the poller saw HEAD unchanged.
    assert sorted(json.loads(j.config_json).get("trigger", "") for j in jobs) == ["", "webhook"]


# ---------------------------------------------------------------------------
# Refusals: the checkout and the index stay as they were
# ---------------------------------------------------------------------------


async def test_a_failed_fetch_fails_the_job_and_leaves_the_index(
    app, session_factory, remote, no_provider
) -> None:
    checkout = _checkout(remote, managed=True)
    repo_id = await _register(session_factory, checkout)
    assert (await _run_job(session_factory, app.state, repo_id, "index_only")).status == "completed"
    _commit(remote.seed, "b.py", "x = 1\n")
    _git(checkout, "remote", "set-url", "origin", str(remote.root / "gone.git"))

    job = await _run_job(session_factory, app.state, repo_id, "sync")

    assert job.status == "failed"
    assert "could not fetch origin" in job.error_message
    assert "The index was left as it was" in job.error_message
    assert _git(checkout, "rev-parse", "HEAD") == remote.first
    assert _state_commit(checkout) == remote.first


async def test_edited_tracked_files_are_kept_and_the_sync_refused(
    app, session_factory, remote, no_provider
) -> None:
    checkout = _checkout(remote, managed=True)
    repo_id = await _register(session_factory, checkout)
    assert (await _run_job(session_factory, app.state, repo_id, "index_only")).status == "completed"
    _commit(remote.seed, "b.py", "x = 1\n")
    (checkout / "a.py").write_text("local edit\n", encoding="utf-8")

    job = await _run_job(session_factory, app.state, repo_id, "sync")

    assert job.status == "failed"
    assert "edited tracked files" in job.error_message
    assert (checkout / "a.py").read_text(encoding="utf-8") == "local edit\n"
    assert _git(checkout, "rev-parse", "HEAD") == remote.first
    assert _state_commit(checkout) == remote.first


def test_local_commits_are_kept_and_the_move_refused(remote) -> None:
    checkout = _checkout(remote, managed=True)
    _commit(remote.seed, "b.py", "x = 1\n")
    (checkout / "local.py").write_text("y = 2\n", encoding="utf-8")
    _git(checkout, "add", "local.py")
    _git(checkout, "commit", "-q", "-m", "local")
    local = _git(checkout, "rev-parse", "HEAD")

    with pytest.raises(ManagedCheckoutError, match="has commits origin does not"):
        fast_forward(checkout)
    assert _git(checkout, "rev-parse", "HEAD") == local


def test_a_detached_head_is_refused(remote) -> None:
    checkout = _checkout(remote, managed=True)
    _git(checkout, "checkout", "-q", "--detach")
    with pytest.raises(ManagedCheckoutError, match="detached"):
        fast_forward(checkout)
    with pytest.raises(ManagedCheckoutError, match="detached"):
        remote_head(checkout)


def test_remote_head_reads_the_remote_without_fetching(remote) -> None:
    checkout = _checkout(remote, managed=True)
    pushed = _commit(remote.seed, "b.py", "x = 1\n")
    assert remote_head(checkout) == pushed
    assert _tracking_ref(checkout) == remote.first


def test_only_an_explicit_setting_makes_a_checkout_managed(remote) -> None:
    checkout = _checkout(remote, managed=False)
    assert not is_managed_checkout(checkout)
    save_repo_config(checkout, {"managed_checkout": "false"})
    assert not is_managed_checkout(checkout)
    save_repo_config(checkout, {"managed_checkout": True})
    assert is_managed_checkout(checkout)
