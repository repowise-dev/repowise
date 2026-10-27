"""Tests for repowise.core.workspace.update — staleness and update orchestration."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from repowise.core.ingestion.git_indexer import GIT_HISTORY_VERSION, GIT_HISTORY_VERSION_KEY
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.update import (
    RepoUpdateResult,
    _collect_update_results,
    check_repo_staleness,
    count_commits_between,
    get_head_commit,
    read_state_commit,
    run_cross_repo_hooks,
    sync_workspace_state_from_disk,
    update_workspace,
)

# ---------------------------------------------------------------------------
# sync_workspace_state_from_disk — Phase C1
# ---------------------------------------------------------------------------


class TestSyncWorkspaceStateFromDisk:
    def test_picks_up_drifted_state_json(self, tmp_path: Path) -> None:
        """When a child repo is updated outside the workspace orchestrator,
        the workspace config drifts. sync_workspace_state_from_disk pulls
        the real value from state.json."""
        repo = _make_git_repo(tmp_path, "backend")
        new_sha = "deadbeef" * 5  # 40 chars
        _write_state(repo, new_sha)

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(
                path="backend",
                alias="backend",
                last_commit_at_index="stale-sha",
            )],
        )
        ws_config.save(tmp_path)

        changed = sync_workspace_state_from_disk(tmp_path, ws_config)
        assert changed == ["backend"]
        assert ws_config.get_repo("backend").last_commit_at_index == new_sha

    def test_no_change_when_in_sync(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "backend")
        sha = "a" * 40
        _write_state(repo, sha)
        ws_config = WorkspaceConfig(
            repos=[RepoEntry(
                path="backend", alias="backend", last_commit_at_index=sha,
            )],
        )
        ws_config.save(tmp_path)
        assert sync_workspace_state_from_disk(tmp_path, ws_config) == []

    def test_missing_dir_skipped(self, tmp_path: Path) -> None:
        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="ghost", alias="ghost")],
        )
        assert sync_workspace_state_from_disk(tmp_path, ws_config) == []

    def test_missing_state_json_skipped(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path, "backend")
        ws_config = WorkspaceConfig(
            repos=[RepoEntry(
                path="backend", alias="backend", last_commit_at_index="old",
            )],
        )
        # No state.json written → entry preserved unchanged
        assert sync_workspace_state_from_disk(tmp_path, ws_config) == []
        assert ws_config.get_repo("backend").last_commit_at_index == "old"


class TestReadStateCommit:
    def test_returns_commit(self, tmp_path: Path) -> None:
        repo = tmp_path / "r"
        repo.mkdir()
        _write_state(repo, "abc123")
        assert read_state_commit(repo) == "abc123"

    def test_missing_returns_none(self, tmp_path: Path) -> None:
        assert read_state_commit(tmp_path) is None

    def test_malformed_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / ".repowise").mkdir()
        (tmp_path / ".repowise" / "state.json").write_text("not json{")
        assert read_state_commit(tmp_path) is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_repo(tmp_path: Path, name: str) -> Path:
    """Create a real git repo with an initial commit."""
    repo = tmp_path / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), capture_output=True,
    )
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(repo), capture_output=True,
    )
    return repo


def _add_commit(repo: Path, filename: str = "change.txt", msg: str = "update") -> str:
    """Add a file and commit. Returns the new commit SHA."""
    (repo / filename).write_text(f"change-{msg}")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=str(repo), capture_output=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), capture_output=True, text=True,
    )
    return result.stdout.strip()


def _write_state(repo: Path, commit: str | None) -> None:
    """Write a state.json with the given commit."""
    state_dir = repo / ".repowise"
    state_dir.mkdir(parents=True, exist_ok=True)
    state = {}
    if commit:
        state["last_sync_commit"] = commit
        state[GIT_HISTORY_VERSION_KEY] = GIT_HISTORY_VERSION
    (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


# ---------------------------------------------------------------------------
# get_head_commit
# ---------------------------------------------------------------------------


class TestGetHeadCommit:
    def test_returns_sha(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        head = get_head_commit(repo)
        assert head is not None
        assert len(head) == 40

    def test_non_git_returns_none(self, tmp_path: Path) -> None:
        assert get_head_commit(tmp_path) is None


# ---------------------------------------------------------------------------
# check_repo_staleness
# ---------------------------------------------------------------------------


class TestCheckRepoStaleness:
    def test_same_commit_not_stale(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        head = get_head_commit(repo)
        is_stale, current, behind = check_repo_staleness(repo, head)
        assert is_stale is False
        assert current == head
        assert behind == 0

    def test_different_commit_stale(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        old_head = get_head_commit(repo)
        _add_commit(repo, "file1.txt", "second commit")
        is_stale, current, behind = check_repo_staleness(repo, old_head)
        assert is_stale is True
        assert current != old_head
        assert behind == 1

    def test_no_stored_commit_stale(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        is_stale, current, _behind = check_repo_staleness(repo, None)
        assert is_stale is True
        assert current is not None

    def test_multiple_commits_behind(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        old_head = get_head_commit(repo)
        _add_commit(repo, "a.txt", "commit 2")
        _add_commit(repo, "b.txt", "commit 3")
        _add_commit(repo, "c.txt", "commit 4")
        is_stale, _current, behind = check_repo_staleness(repo, old_head)
        assert is_stale is True
        assert behind == 3

    def test_non_git_dir_not_stale(self, tmp_path: Path) -> None:
        is_stale, current, _behind = check_repo_staleness(tmp_path, "abc123")
        assert is_stale is False
        assert current is None


# ---------------------------------------------------------------------------
# count_commits_between
# ---------------------------------------------------------------------------


class TestCountCommitsBetween:
    def test_counts_correctly(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        old = get_head_commit(repo)
        _add_commit(repo, "a.txt")
        _add_commit(repo, "b.txt")
        new = get_head_commit(repo)
        assert count_commits_between(repo, old, new) == 2

    def test_zero_when_same(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path, "myrepo")
        head = get_head_commit(repo)
        assert count_commits_between(repo, head, head) == 0


# ---------------------------------------------------------------------------
# update_workspace
# ---------------------------------------------------------------------------


class TestUpdateWorkspace:
    def test_skips_up_to_date(self, tmp_path: Path) -> None:
        """Repos with same HEAD should be skipped."""
        repo = _make_git_repo(tmp_path, "backend")
        head = get_head_commit(repo)
        _write_state(repo, head)

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=head)],
            default_repo="backend",
        )
        ws_config.save(tmp_path)

        async def _run():
            return await update_workspace(tmp_path, ws_config)

        import asyncio
        results = asyncio.run(_run())
        assert len(results) == 1
        assert results[0].updated is False
        assert results[0].skipped_reason == "up_to_date"

    def test_a_caller_that_restamped_leaves_up_to_date_members_untouched(
        self, tmp_path: Path
    ) -> None:
        """The CLI restamps the up-to-date members whose fingerprint moved
        before the pass; the pass must not reopen every other member's store,
        which would rewrite each one (and its editor files) on every change.
        The member that changed is still stamped."""
        import asyncio

        backend = _make_git_repo(tmp_path, "backend")
        backend_head = get_head_commit(backend)
        _write_state(backend, backend_head)
        frontend = _make_git_repo(tmp_path, "frontend")
        old_head = get_head_commit(frontend)
        _write_state(frontend, old_head)
        new_head = _add_commit(frontend, "page.txt")
        ws_config = WorkspaceConfig(
            repos=[
                RepoEntry(path="backend", alias="backend", last_commit_at_index=backend_head),
                RepoEntry(path="frontend", alias="frontend", last_commit_at_index=old_head),
            ],
        )
        ws_config.save(tmp_path)
        stamp = AsyncMock()

        async def _run():
            with (
                patch(
                    "repowise.core.workspace.update.workspace_pass.update_single_repo_index",
                    new_callable=AsyncMock,
                    return_value=RepoUpdateResult(alias="frontend", updated=True),
                ),
                patch(
                    "repowise.core.workspace.update.workspace_pass.reconcile_repo_head_commit",
                    stamp,
                ),
            ):
                return await update_workspace(
                    tmp_path, ws_config, run_hooks=False, restamp_up_to_date=False
                )

        results = asyncio.run(_run())
        assert {r.alias: r.skipped_reason for r in results} == {
            "backend": "up_to_date",
            "frontend": None,
        }
        assert [c.args for c in stamp.await_args_list] == [(frontend.resolve(), new_head)]

    def test_up_to_date_repo_reconciles_freshness_stamp(self, tmp_path: Path) -> None:
        """An up-to-date workspace sync still refreshes the repo row timestamp."""
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
        )
        from repowise.core.persistence.crud import get_repository_by_path, upsert_repository
        from repowise.core.persistence.database import resolve_db_url

        repo = _make_git_repo(tmp_path, "backend")
        head = get_head_commit(repo)
        _write_state(repo, head)

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=head)],
            default_repo="backend",
        )
        ws_config.save(tmp_path)

        stale_dt = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

        async def _seed() -> None:
            engine = create_engine(resolve_db_url(repo))
            await init_db(engine)
            sf = create_session_factory(engine)
            try:
                async with get_session(sf) as session:
                    row = await upsert_repository(
                        session,
                        name="backend",
                        local_path=str(repo),
                        head_commit="deadbeef" + "0" * 32,
                    )
                    row.updated_at = stale_dt
                    await session.flush()
            finally:
                await engine.dispose()

        async def _read_back():
            engine = create_engine(resolve_db_url(repo))
            sf = create_session_factory(engine)
            try:
                async with get_session(sf) as session:
                    return await get_repository_by_path(session, str(repo))
            finally:
                await engine.dispose()

        async def _run():
            await _seed()
            from repowise.core.repo_config import (
                config_dependency_fingerprints,
                config_fingerprint,
            )

            state_path = repo / ".repowise" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["config_fingerprint"] = config_fingerprint(repo)
            state["config_dependency_fingerprints"] = config_dependency_fingerprints(repo)
            state_path.write_text(json.dumps(state), encoding="utf-8")
            return await update_workspace(tmp_path, ws_config)

        import asyncio

        results = asyncio.run(_run())
        row = asyncio.run(_read_back())
        assert len(results) == 1
        assert results[0].updated is False
        assert results[0].skipped_reason == "up_to_date"
        assert row is not None
        assert row.head_commit == head
        assert row.updated_at.replace(tzinfo=UTC) > stale_dt

    def test_detects_stale_repo(self, tmp_path: Path) -> None:
        """Repos with new commits should be detected as stale."""
        repo = _make_git_repo(tmp_path, "backend")
        old_head = get_head_commit(repo)
        _write_state(repo, old_head)
        _add_commit(repo, "new_file.txt")

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=old_head)],
            default_repo="backend",
        )
        ws_config.save(tmp_path)

        # Mock update_single_repo_index to avoid running the full pipeline
        mock_result = RepoUpdateResult(alias="backend", updated=True, file_count=10, symbol_count=50)

        async def _run():
            with patch(
                "repowise.core.workspace.update.workspace_pass.update_single_repo_index",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                return await update_workspace(tmp_path, ws_config)

        import asyncio
        results = asyncio.run(_run())
        updated = [r for r in results if r.updated]
        assert len(updated) == 1
        assert updated[0].alias == "backend"
        saved_state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
        assert saved_state["phase_timings"]["run"] >= 0

    def test_reports_stored_counts_not_run_figures(self, tmp_path: Path) -> None:
        """A commit with nothing to re-parse reports 0/0 from the run; the result
        carries the store's files and symbols, the figures `workspace list` shows."""
        import asyncio

        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )
        from repowise.core.persistence.database import resolve_db_url
        from repowise.core.persistence.models import GraphNode
        from tests.unit.persistence.helpers import make_wiki_symbol

        repo = _make_git_repo(tmp_path, "backend")
        old_head = get_head_commit(repo)
        _write_state(repo, old_head)
        _add_commit(repo, "notes.txt")
        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=old_head)],
        )
        ws_config.save(tmp_path)

        async def _seed() -> None:
            engine = create_engine(resolve_db_url(repo))
            await init_db(engine)
            try:
                async with get_session(create_session_factory(engine)) as session:
                    row = await upsert_repository(
                        session, name="backend", local_path=str(repo), head_commit=old_head
                    )
                    session.add_all([
                        GraphNode(repository_id=row.id, node_id="a.py", language="python"),
                        GraphNode(repository_id=row.id, node_id="b.py", language="python"),
                        # An import target is not an indexed file.
                        GraphNode(repository_id=row.id, node_id="requests", language="external"),
                        *(make_wiki_symbol(row.id, "a.py", n) for n in ("f", "g", "h")),
                    ])
                    await session.flush()
            finally:
                await engine.dispose()

        asyncio.run(_seed())
        done: list[RepoUpdateResult] = []

        async def _run():
            with patch(
                "repowise.core.workspace.update.workspace_pass.update_single_repo_index",
                new_callable=AsyncMock,
                return_value=RepoUpdateResult(alias="backend", updated=True),
            ):
                return await update_workspace(
                    tmp_path, ws_config, run_hooks=False, on_repo_done=done.append
                )

        results = asyncio.run(_run())
        assert (results[0].file_count, results[0].symbol_count) == (2, 3)
        assert (done[0].file_count, done[0].symbol_count) == (2, 3)

    def test_detects_config_only_staleness(self, tmp_path: Path) -> None:
        """A workspace member with no new commit still runs when its local
        configuration changed."""
        from repowise.core.repo_config import config_fingerprint, save_repo_config

        repo = _make_git_repo(tmp_path, "backend")
        head = get_head_commit(repo)
        save_repo_config(repo, {"provider": "mock"})
        _write_state(repo, head)
        state_path = repo / ".repowise" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["config_fingerprint"] = config_fingerprint(repo)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        save_repo_config(repo, {"provider": "openai"})

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=head)]
        )
        mock_result = RepoUpdateResult(alias="backend", updated=True, file_count=1, symbol_count=1)

        async def _run():
            with patch(
                "repowise.core.workspace.update.workspace_pass.update_single_repo_index",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mocked:
                results = await update_workspace(tmp_path, ws_config)
                return results, mocked.await_count

        import asyncio

        results, calls = asyncio.run(_run())
        assert calls == 1
        assert results[0].updated is True
        saved_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert saved_state["phase_timings"]["run"] >= 0

    def test_legacy_config_fingerprints_are_backfilled_then_detect_changes(
        self, tmp_path: Path
    ) -> None:
        from repowise.core.repo_config import save_repo_config

        repo = _make_git_repo(tmp_path, "backend")
        head = get_head_commit(repo)
        _write_state(repo, head)
        (repo / ".repowise" / "wiki.db").touch()
        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=head)]
        )
        mock_result = RepoUpdateResult(alias="backend", updated=True, file_count=1)

        async def _run() -> int:
            with patch(
                "repowise.core.workspace.update.workspace_pass.update_single_repo_index",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mocked:
                await update_workspace(tmp_path, ws_config)
                save_repo_config(repo, {"future_setting": True})
                await update_workspace(tmp_path, ws_config)
                return mocked.await_count

        import asyncio

        assert asyncio.run(_run()) == 2
        state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
        assert state.get("config_fingerprint")
        assert state.get("config_dependency_fingerprints")

    def test_shared_db_missing_fingerprint_is_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A shared-database repo with a row and no config fingerprint is stale.

        Dry run omits stale repos, so an empty result list is the verdict.
        """
        repo = _make_git_repo(tmp_path, "backend")
        head = get_head_commit(repo)
        _write_state(repo, head)
        assert not (repo / ".repowise" / "wiki.db").exists()

        shared_db = tmp_path / "shared.db"
        db_url = f"sqlite+aiosqlite:///{shared_db}"
        monkeypatch.setenv("REPOWISE_DB_URL", db_url)

        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )

        async def _seed() -> None:
            engine = create_engine(db_url)
            try:
                await init_db(engine)
                sf = create_session_factory(engine)
                async with get_session(sf) as session:
                    await upsert_repository(
                        session,
                        name=repo.name,
                        local_path=str(repo.resolve()),
                        head_commit=head,
                    )
            finally:
                await engine.dispose()

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=head)],
        )

        async def _run():
            await _seed()
            return await update_workspace(
                tmp_path, ws_config, dry_run=True, run_hooks=False
            )

        import asyncio

        results = asyncio.run(_run())
        assert results == []

    def test_shared_db_missing_row_without_fingerprint_stays_up_to_date(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No shared-database row keeps a missing fingerprint off the stale path."""
        repo = _make_git_repo(tmp_path, "backend")
        head = get_head_commit(repo)
        _write_state(repo, head)
        assert not (repo / ".repowise" / "wiki.db").exists()

        shared_db = tmp_path / "shared.db"
        db_url = f"sqlite+aiosqlite:///{shared_db}"
        monkeypatch.setenv("REPOWISE_DB_URL", db_url)

        from repowise.core.persistence import create_engine, init_db

        async def _init_schema() -> None:
            engine = create_engine(db_url)
            try:
                await init_db(engine)
            finally:
                await engine.dispose()

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=head)],
        )

        async def _run():
            await _init_schema()
            return await update_workspace(
                tmp_path, ws_config, dry_run=True, run_hooks=False
            )

        import asyncio

        results = asyncio.run(_run())
        assert len(results) == 1
        assert results[0].updated is False
        assert results[0].skipped_reason == "up_to_date"

    def test_repo_filter(self, tmp_path: Path) -> None:
        """--repo flag should only update the specified repo."""
        repo_a = _make_git_repo(tmp_path, "backend")
        repo_b = _make_git_repo(tmp_path, "frontend")
        old_a = get_head_commit(repo_a)
        old_b = get_head_commit(repo_b)
        _write_state(repo_a, old_a)
        _write_state(repo_b, old_b)
        _add_commit(repo_a, "a.txt")
        _add_commit(repo_b, "b.txt")

        ws_config = WorkspaceConfig(
            repos=[
                RepoEntry(path="backend", alias="backend", last_commit_at_index=old_a),
                RepoEntry(path="frontend", alias="frontend", last_commit_at_index=old_b),
            ],
            default_repo="backend",
        )
        ws_config.save(tmp_path)

        mock_result = RepoUpdateResult(alias="backend", updated=True, file_count=5, symbol_count=20)

        async def _run():
            with patch(
                "repowise.core.workspace.update.workspace_pass.update_single_repo_index",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                return await update_workspace(tmp_path, ws_config, repo_filter="backend")

        import asyncio
        results = asyncio.run(_run())
        # Only backend should appear (frontend filtered out)
        assert len(results) == 1
        assert results[0].alias == "backend"

    def test_invalid_repo_filter_raises(self, tmp_path: Path) -> None:
        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend")],
        )

        async def _run():
            return await update_workspace(tmp_path, ws_config, repo_filter="nonexistent")

        import asyncio
        with pytest.raises(ValueError, match="Unknown repo"):
            asyncio.run(_run())

    def test_first_time_index_runs_pipeline(self, tmp_path: Path) -> None:
        """Repos without .repowise/ now get first-time indexing rather
        than being silently skipped (Phase C1)."""
        repo = _make_git_repo(tmp_path, "backend")
        # No .repowise/ dir, no state.json

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend")],
        )

        async def _run():
            return await update_workspace(tmp_path, ws_config)

        import asyncio
        results = asyncio.run(_run())
        assert len(results) == 1
        # The pipeline ran (may have errored due to empty repo, but the
        # important contract is: we did NOT short-circuit with skipped_reason).
        assert results[0].skipped_reason != "not_indexed"
        # Either the pipeline succeeded (updated=True, first_time flagged)
        # or it errored — both prove we didn't bail early.
        if results[0].updated:
            assert results[0].first_time_indexed is True
            assert (repo / ".repowise").is_dir()

    def test_dry_run(self, tmp_path: Path) -> None:
        """Dry run should not perform any updates."""
        repo = _make_git_repo(tmp_path, "backend")
        old_head = get_head_commit(repo)
        _write_state(repo, old_head)
        _add_commit(repo, "new.txt")

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=old_head)],
        )

        async def _run():
            return await update_workspace(tmp_path, ws_config, dry_run=True)

        import asyncio
        results = asyncio.run(_run())
        # Stale repos detected but not updated
        updated = [r for r in results if r.updated]
        assert len(updated) == 0

    @pytest.mark.parametrize("content", ["[]", '"x"', "null", "0"])
    def test_dry_run_tolerates_a_state_file_that_is_not_an_object(
        self, tmp_path: Path, content: str
    ) -> None:
        """A member whose state.json is valid JSON but not an object must not block the workspace."""
        _make_git_repo(tmp_path, "api")
        web = _make_git_repo(tmp_path, "web")
        (web / ".repowise").mkdir(exist_ok=True)
        (web / ".repowise" / "state.json").write_text(content, encoding="utf-8")

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="api", alias="api"), RepoEntry(path="web", alias="web")],
        )

        import asyncio

        results = asyncio.run(update_workspace(tmp_path, ws_config, dry_run=True, run_hooks=False))
        assert isinstance(results, list)  # reaching here is the point: it used to raise AttributeError

    def test_write_repo_state_replaces_a_state_file_that_is_not_an_object(
        self, tmp_path: Path
    ) -> None:
        from repowise.core.workspace.update import RepoUpdateResult, _write_repo_state

        repo = _make_git_repo(tmp_path, "web")
        head = get_head_commit(repo)
        (repo / ".repowise").mkdir(exist_ok=True)
        (repo / ".repowise" / "state.json").write_text("[]", encoding="utf-8")

        result = RepoUpdateResult(alias="web", updated=True)
        _write_repo_state(repo, "web", head, False, result)

        state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
        assert state["last_sync_commit"] == head

    @pytest.mark.parametrize(
        "failure_stage",
        ["start", "mkdir", "lock", "index", "state", "stamp", "pending"],
    )
    def test_repo_failure_preserves_alias_and_notifies_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str
    ) -> None:
        """A failed member is named and reported without losing successful siblings."""
        import asyncio

        from repowise.core.update_lock import update_lock_path, workspace_update_lock_path
        from repowise.core.workspace.update import workspace_pass

        repos = [_make_git_repo(tmp_path, name) for name in ("web", "core")]
        entries = []
        for repo in repos:
            old_head = get_head_commit(repo)
            _write_state(repo, old_head)
            _add_commit(repo, "new.txt")
            entries.append(RepoEntry(path=repo.name, alias=repo.name, last_commit_at_index=old_head))
        ws_config = WorkspaceConfig(repos=entries)
        ws_config.save(tmp_path)
        done_results: list[RepoUpdateResult] = []

        async def fake_update(path, **_):
            return RepoUpdateResult(alias="", updated=True, file_count=1, symbol_count=2)

        monkeypatch.setattr(workspace_pass, "update_single_repo_index", fake_update)
        monkeypatch.setattr(workspace_pass, "reconcile_repo_head_commit", AsyncMock())

        def on_start(alias):
            if failure_stage == "start" and alias == "web":
                raise OSError("disk full")

        if failure_stage == "mkdir":
            real_mkdir = Path.mkdir

            def fail_mkdir(path, *args, **kwargs):
                if path == repos[0] / ".repowise":
                    raise OSError("disk full")
                return real_mkdir(path, *args, **kwargs)

            monkeypatch.setattr(Path, "mkdir", fail_mkdir)
        elif failure_stage in {"index", "stamp"}:
            name = "update_single_repo_index" if failure_stage == "index" else "reconcile_repo_head_commit"
            original = getattr(workspace_pass, name)

            async def fail_async(path, *args, **kwargs):
                if path == repos[0]:
                    raise OSError("disk full")
                return await original(path, *args, **kwargs)

            monkeypatch.setattr(workspace_pass, name, fail_async)
        elif failure_stage in {"lock", "state", "pending"}:
            name = {
                "lock": "_try_acquire_lock",
                "state": "_write_repo_state",
                "pending": "clear_stale_update_pending",
            }[failure_stage]
            original = getattr(workspace_pass, name)

            def fail_sync(path, *args, **kwargs):
                if path == repos[0]:
                    raise OSError("disk full")
                return original(path, *args, **kwargs)

            monkeypatch.setattr(workspace_pass, name, fail_sync)

        results = asyncio.run(update_workspace(
            tmp_path, ws_config, run_hooks=False,
            on_repo_start=on_start, on_repo_done=done_results.append,
        ))
        assert [r.alias for r in results] == ["web", "core"]
        assert results[0].updated is False
        assert results[0].error == "disk full"
        assert results[1].updated is True
        assert results[1].error is None
        assert (results[1].file_count, results[1].symbol_count) == (1, 2)
        assert len(done_results) == 2
        assert {r.alias: r for r in done_results} == {r.alias: r for r in results}
        assert not workspace_update_lock_path(tmp_path).exists()
        assert all(not update_lock_path(repo).exists() for repo in repos)

    @pytest.mark.parametrize("index_error", [None, "index failed"])
    def test_completion_callback_failure_preserves_alias_and_original_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog, index_error: str | None
    ) -> None:
        """A broken reporter is called once; it cannot replace the real failure."""
        import asyncio
        import logging

        from repowise.core.workspace.update import workspace_pass

        repo = _make_git_repo(tmp_path, "web")
        old_head = get_head_commit(repo)
        _write_state(repo, old_head)
        _add_commit(repo, "new.txt")
        ws_config = WorkspaceConfig(repos=[RepoEntry(path="web", alias="web")])
        done_results: list[RepoUpdateResult] = []

        async def fake_update(path, **_):
            return RepoUpdateResult(alias="", updated=index_error is None, error=index_error)

        def on_done(result):
            done_results.append(result)
            raise OSError("progress output failed")

        monkeypatch.setattr(workspace_pass, "update_single_repo_index", fake_update)
        monkeypatch.setattr(workspace_pass, "reconcile_repo_head_commit", AsyncMock())
        caplog.set_level(logging.WARNING, logger="repowise.workspace.update")
        results = asyncio.run(update_workspace(
            tmp_path, ws_config, run_hooks=False, on_repo_done=on_done,
        ))
        assert len(results) == 1
        assert results[0].alias == "web"
        assert results[0].updated is False
        assert results[0].error == (index_error or "progress output failed")
        assert len(done_results) == 1
        assert "web" in caplog.text
        assert "progress output failed" in caplog.text

    @pytest.mark.parametrize("cancel_stage", ["start", "index", "stamp", "done"])
    def test_member_cancellation_propagates_and_releases_both_locks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_stage: str
    ) -> None:
        import asyncio

        from repowise.core.update_lock import update_lock_path, workspace_update_lock_path
        from repowise.core.workspace.update import workspace_pass

        repo = _make_git_repo(tmp_path, "backend")
        old_head = get_head_commit(repo)
        _write_state(repo, old_head)
        _add_commit(repo, "new.txt")
        ws_config = WorkspaceConfig(repos=[RepoEntry(path="backend", alias="backend")])
        hooks = AsyncMock()
        monkeypatch.setattr(workspace_pass, "run_cross_repo_hooks", hooks)

        def cancel():
            assert workspace_update_lock_path(tmp_path).exists()
            assert update_lock_path(repo).exists() == (cancel_stage == "index")
            raise asyncio.CancelledError()

        def on_start(_alias):
            if cancel_stage == "start":
                cancel()

        def on_done(_result):
            if cancel_stage == "done":
                cancel()

        async def fake_update(path, **_):
            if cancel_stage == "index":
                cancel()
            return RepoUpdateResult(alias="", updated=True)

        async def stamp(*_):
            if cancel_stage == "stamp":
                cancel()

        monkeypatch.setattr(workspace_pass, "update_single_repo_index", fake_update)
        monkeypatch.setattr(workspace_pass, "reconcile_repo_head_commit", stamp)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(update_workspace(
                tmp_path, ws_config, on_repo_start=on_start, on_repo_done=on_done,
            ))
        assert not workspace_update_lock_path(tmp_path).exists()
        assert not update_lock_path(repo).exists()
        hooks.assert_not_awaited()

    def test_cancelling_workspace_task_releases_all_member_locks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancelling gather itself cleans up every running member before returning."""
        import asyncio

        from repowise.core.update_lock import update_lock_path, workspace_update_lock_path
        from repowise.core.workspace.update import workspace_pass

        repos = [_make_git_repo(tmp_path, name) for name in ("web", "core")]
        for repo in repos:
            _write_state(repo, get_head_commit(repo))
            _add_commit(repo, "new.txt")
        ws_config = WorkspaceConfig(repos=[RepoEntry(path=r.name, alias=r.name) for r in repos])
        done = []

        async def run():
            started = set()
            all_started = asyncio.Event()
            blocked = asyncio.Event()

            async def fake_update(path, **_):
                assert update_lock_path(path).exists()
                started.add(path)
                if len(started) == len(repos):
                    all_started.set()
                await blocked.wait()

            monkeypatch.setattr(workspace_pass, "update_single_repo_index", fake_update)
            task = asyncio.create_task(update_workspace(
                tmp_path, ws_config, run_hooks=False, on_repo_done=done.append,
            ))
            try:
                await asyncio.wait_for(all_started.wait(), timeout=5)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        asyncio.run(run())
        assert done == []
        assert not workspace_update_lock_path(tmp_path).exists()
        assert all(not update_lock_path(repo).exists() for repo in repos)


# ---------------------------------------------------------------------------
# _collect_update_results
# ---------------------------------------------------------------------------


class TestCollectUpdateResults:
    @pytest.mark.parametrize("fatal", ["cancel", "interrupt", "exit"])
    @pytest.mark.parametrize("fatal_first", [False, True])
    def test_fatal_result_propagates_before_mutating_results(self, fatal: str, fatal_first: bool) -> None:
        import asyncio

        exception = {
            "cancel": asyncio.CancelledError(),
            "interrupt": KeyboardInterrupt(),
            "exit": SystemExit(),
        }[fatal]
        previous = RepoUpdateResult(alias="previous", updated=False, skipped_reason="up_to_date")
        results = [previous]
        success = RepoUpdateResult(alias="web", updated=True)
        gathered = [exception, success] if fatal_first else [success, exception]
        with pytest.raises(type(exception)) as raised:
            _collect_update_results(gathered, results)
        assert raised.value is exception
        assert results == [previous]

    def test_collects_results_in_input_order_with_last_resort_exception(self) -> None:
        previous = RepoUpdateResult(alias="previous", updated=False)
        web = RepoUpdateResult(alias="web", updated=True)
        skipped = RepoUpdateResult(alias="skipped", updated=False, skipped_reason="in_flight")
        core = RepoUpdateResult(alias="core", updated=True)
        results = [previous]
        changed = _collect_update_results([web, ValueError("disk full"), skipped, core], results)
        assert changed == ["web", "core"]
        assert [r.alias for r in results] == ["previous", "web", "unknown", "skipped", "core"]
        assert results[1] is web
        assert results[2].updated is False
        assert results[2].error == "disk full"
        assert results[3] is skipped
        assert results[4] is core


# ---------------------------------------------------------------------------
# Workspace-level single-flight (issue #1831)
# ---------------------------------------------------------------------------


class TestWorkspaceSingleFlight:
    def test_concurrent_run_defers_to_owner(self, tmp_path: Path) -> None:
        """A workspace update that finds another workspace update already in
        flight must defer (skipped_reason=\"in_flight\") instead of running a
        redundant full pass over every member."""
        repo = _make_git_repo(tmp_path, "backend")
        old_head = get_head_commit(repo)
        _write_state(repo, old_head)
        _add_commit(repo, "new.txt")

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=old_head)],
            default_repo="backend",
        )
        ws_config.save(tmp_path)

        from repowise.core.update_lock import update_workspace_lock

        # Simulate another workspace update already in flight (same process).
        assert update_workspace_lock(tmp_path) is None
        try:
            mock_result = RepoUpdateResult(alias="backend", updated=True, file_count=1, symbol_count=1)

            async def _run():
                with patch(
                    "repowise.core.workspace.update.workspace_pass.update_single_repo_index",
                    new_callable=AsyncMock,
                    return_value=mock_result,
                ):
                    return await update_workspace(tmp_path, ws_config)

            import asyncio
            results = asyncio.run(_run())
            # No repo should have been updated — the whole pass deferred.
            assert len(results) == 1
            assert results[0].updated is False
            assert results[0].skipped_reason == "in_flight"
            # The running owner should pick up the deferred head via the
            # pending marker written by the defereer.
            pending = (tmp_path / "backend" / ".repowise" / ".update.pending")
            assert pending.exists()
            assert pending.read_text(encoding="utf-8") == get_head_commit(tmp_path / "backend")
        finally:
            from repowise.core.update_lock import release_workspace_lock

            release_workspace_lock(tmp_path)

    def test_lock_released_after_pass(self, tmp_path: Path) -> None:
        """update_workspace must release the workspace lock after a successful
        pass so the next hook-triggered invocation can take over."""
        repo = _make_git_repo(tmp_path, "backend")
        old_head = get_head_commit(repo)
        _write_state(repo, old_head)
        _add_commit(repo, "new.txt")

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="backend", alias="backend", last_commit_at_index=old_head)],
            default_repo="backend",
        )
        ws_config.save(tmp_path)

        mock_result = RepoUpdateResult(alias="backend", updated=True, file_count=1, symbol_count=1)

        from repowise.core.update_lock import workspace_update_lock_path

        async def _run():
            with patch(
                "repowise.core.workspace.update.workspace_pass.update_single_repo_index",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                return await update_workspace(tmp_path, ws_config)

        import asyncio
        asyncio.run(_run())

        # The workspace lock was created during the pass and released at the end.
        assert not workspace_update_lock_path(tmp_path).exists()


# ---------------------------------------------------------------------------
# Cross-repo hooks placeholder
# ---------------------------------------------------------------------------


class TestCrossRepoHooks:
    def test_runs_without_error(self, tmp_path: Path) -> None:
        """Placeholder hook should be a no-op."""
        ws_config = WorkspaceConfig(repos=[])

        import asyncio
        asyncio.run(run_cross_repo_hooks(ws_config, tmp_path, ["backend"]))

    def test_measures_contract_facts_of_the_changed_repos_last(self, tmp_path: Path) -> None:
        """The snapshot each update took ran before extraction, so the hooks
        end by filling its contract facts, for the repos that changed only."""
        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="api", alias="api"), RepoEntry(path="web", alias="web")]
        )
        order: list[str] = []

        async def _phases(*_a, **_k):
            order.append("phases")
            return True, set()

        async def _refresh(path):
            order.append(path.name)

        import asyncio

        with (
            patch("repowise.core.workspace.update.phases._run_phases", _phases),
            patch("repowise.core.workspace.store.refresh_workspace_store", AsyncMock()),
            patch("repowise.core.workspace.update.phases._refresh_contract_facts", _refresh),
        ):
            asyncio.run(run_cross_repo_hooks(ws_config, tmp_path, ["api"]))
        assert order == ["phases", "api"]

    def test_an_unchanged_provider_whose_consumers_moved_is_measured(self, tmp_path: Path) -> None:
        """A change in one repo that adds or drops a call to another repo's
        contract moves that provider's consumer counts: its facts refresh
        too, and no other member's store is opened."""
        ws_config = WorkspaceConfig(
            repos=[
                RepoEntry(path="web", alias="web"),
                RepoEntry(path="api", alias="api"),
                RepoEntry(path="mail", alias="mail"),
            ]
        )
        refreshed: list[str] = []

        async def _phases(*_a, **_k):
            return True, {"api"}

        async def _refresh(path):
            refreshed.append(path.name)

        import asyncio

        with (
            patch("repowise.core.workspace.update.phases._run_phases", _phases),
            patch("repowise.core.workspace.store.refresh_workspace_store", AsyncMock()),
            patch("repowise.core.workspace.update.phases._refresh_contract_facts", _refresh),
        ):
            asyncio.run(run_cross_repo_hooks(ws_config, tmp_path, ["web"]))
        assert refreshed == ["web", "api"]

    def test_consumer_counts_moved_names_only_the_affected_provider(self) -> None:
        from repowise.core.workspace.contracts import Contract, ContractLink, ContractStore
        from repowise.core.workspace.update.phases import _contract_counts_moved

        def _provider(repo: str, cid: str) -> Contract:
            return Contract(repo, cid, "http", "provider", "app.py", "h", 1.0)

        def _link(consumer: str, provider: str, cid: str) -> ContractLink:
            return ContractLink(
                cid, "http", "exact", 1.0, provider, "app.py", "h", None,
                consumer, "client.py", "c", None,
            )

        contracts = [_provider("api", "http::GET::/a"), _provider("mail", "http::POST::/m")]
        before = ContractStore(
            contracts=list(contracts),
            contract_links=[_link("web", "api", "http::GET::/a"), _link("web", "mail", "http::POST::/m")],
        )
        # web drops its call to api; mail's consumers and every contract stay.
        after = ContractStore(
            contracts=list(contracts),
            contract_links=[_link("web", "mail", "http::POST::/m")],
        )
        assert _contract_counts_moved(before, after) == {"api"}
        assert _contract_counts_moved(after, after) == set()
        # A provided contract that appears is a move of its own repo.
        grown = ContractStore(
            contracts=[*contracts, _provider("mail", "http::GET::/n")],
            contract_links=list(after.contract_links),
        )
        assert _contract_counts_moved(after, grown) == {"mail"}

    def test_detect_http_off_still_opens_the_index_for_code_contracts(
        self, tmp_path: Path
    ) -> None:
        """``code`` contracts read the symbol index too, so turning HTTP off
        must not hand extraction an empty index."""
        from repowise.core.workspace.repo_index import WorkspaceIndex

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="api", alias="api"), RepoEntry(path="web", alias="web")]
        )
        ws_config.contracts.detect_http = False
        seen: list[WorkspaceIndex] = []

        async def _phases(_cfg, _root, _changed, _timings, index):
            seen.append(index)
            return True, set()

        opened = WorkspaceIndex({}, {"api": tmp_path / "api"})

        import asyncio

        with (
            patch("repowise.core.workspace.update.phases._run_phases", _phases),
            patch("repowise.core.workspace.store.refresh_workspace_store", AsyncMock()),
            patch(
                "repowise.core.workspace.repo_index.open_workspace_index",
                AsyncMock(return_value=opened),
            ),
        ):
            asyncio.run(run_cross_repo_hooks(ws_config, tmp_path, []))
        assert seen == [opened]


# ---------------------------------------------------------------------------
# RepoUpdateResult
# ---------------------------------------------------------------------------


class TestRepoUpdateResult:
    def test_defaults(self) -> None:
        r = RepoUpdateResult(alias="test", updated=False)
        assert r.file_count == 0
        assert r.symbol_count == 0
        assert r.error is None
        assert r.skipped_reason is None


# ---------------------------------------------------------------------------
# Saved exclude patterns (issue #2947)
# ---------------------------------------------------------------------------


class TestSavedExcludeSources:
    def test_unreadable_shared_database_logs_a_warning(self, tmp_path: Path, caplog) -> None:
        import asyncio
        import logging

        from repowise.core.workspace.update.repo import _configured_settings_json

        caplog.set_level(logging.WARNING, logger="repowise.workspace.update")
        with patch(
            "repowise.core.persistence.create_engine", side_effect=RuntimeError("db unreachable")
        ):
            assert asyncio.run(_configured_settings_json("sqlite+aiosqlite://", tmp_path)) is None
        assert "saved exclude patterns not applied" in caplog.text

    def test_unparseable_settings_json_logs_a_warning(self, caplog) -> None:
        import logging

        from repowise.core.workspace.update.repo import _settings_excludes

        caplog.set_level(logging.WARNING, logger="repowise.workspace.update")
        assert _settings_excludes("{not json") == {}
        assert "saved exclude patterns not applied" in caplog.text


# ---------------------------------------------------------------------------
# Cross-repo settings edited with every repo up to date
# ---------------------------------------------------------------------------


class TestAnalysisConfigChange:
    """An edited contracts: or conformance: block moves no repo, and used to do nothing."""

    def _workspace(self, tmp_path: Path) -> WorkspaceConfig:
        for name in ("api", "web"):
            repo = _make_git_repo(tmp_path, name)
            _write_state(repo, get_head_commit(repo))
        return WorkspaceConfig(
            repos=[RepoEntry(path="api", alias="api"), RepoEntry(path="web", alias="web")]
        )

    def _run(
        self, tmp_path: Path, ws_config: WorkspaceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> AsyncMock:
        import asyncio

        from repowise.core.workspace.update import workspace_pass

        hooks = AsyncMock()
        monkeypatch.setattr(workspace_pass, "run_cross_repo_hooks", hooks)
        asyncio.run(update_workspace(tmp_path, ws_config))
        return hooks

    def test_settings_edit_runs_the_hooks_and_the_stamp_settles_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from repowise.core.workspace.config import ContractConfig
        from repowise.core.workspace.update.phases import stamp_analysis_config

        ws_config = self._workspace(tmp_path)
        stamp_analysis_config(ws_config, tmp_path)
        assert not self._run(tmp_path, ws_config, monkeypatch).await_count

        ws_config.contracts = ContractConfig(service_bases={"API_BASE": "api"})
        hooks = self._run(tmp_path, ws_config, monkeypatch)
        hooks.assert_awaited_once_with(ws_config, tmp_path, [])

    def test_no_stamp_runs_the_hooks_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No stamp: the analysis never ran under these settings, so it runs."""
        ws_config = self._workspace(tmp_path)
        assert self._run(tmp_path, ws_config, monkeypatch).await_count == 1

    def test_run_hooks_false_leaves_them_to_the_caller(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        from repowise.core.workspace.update import workspace_pass

        ws_config = self._workspace(tmp_path)
        hooks = AsyncMock()
        monkeypatch.setattr(workspace_pass, "run_cross_repo_hooks", hooks)
        asyncio.run(update_workspace(tmp_path, ws_config, run_hooks=False))
        hooks.assert_not_awaited()

    def test_hooks_stamp_the_settings_they_ran_with(self, tmp_path: Path) -> None:
        import asyncio

        from repowise.core.workspace.update import analysis_config_changed

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="api", alias="api"), RepoEntry(path="web", alias="web")]
        )
        assert analysis_config_changed(ws_config, tmp_path)
        with (
            patch(
                "repowise.core.workspace.update.phases._run_phases",
                AsyncMock(return_value=(True, set())),
            ),
            patch("repowise.core.workspace.store.refresh_workspace_store", AsyncMock()),
        ):
            asyncio.run(run_cross_repo_hooks(ws_config, tmp_path, []))
        assert not analysis_config_changed(ws_config, tmp_path)

    def test_failed_analysis_is_not_stamped(self, tmp_path: Path) -> None:
        """A failed extraction is retried on the next update, not settled."""
        import asyncio

        from repowise.core.workspace.update import analysis_config_changed

        ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="api", alias="api"), RepoEntry(path="web", alias="web")]
        )
        with (
            patch(
                "repowise.core.workspace.update.phases._run_phases",
                AsyncMock(return_value=(False, set())),
            ),
            patch("repowise.core.workspace.store.refresh_workspace_store", AsyncMock()),
        ):
            asyncio.run(run_cross_repo_hooks(ws_config, tmp_path, []))
        assert analysis_config_changed(ws_config, tmp_path)
