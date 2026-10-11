"""Incremental updates must refresh ``external_systems`` (C4 L1) on manifest change.

The incremental update path historically never re-extracted external systems, so
the C4 architecture panel served the init-time dependency list until a full
re-index. These tests drive the real ``refresh_external_systems`` helper over a
repo whose ``package.json`` changes and assert: a manifest change re-extracts and
reconciles the table (added deps appear, removed deps pruned), and a change that
touches no manifest is a no-op that never re-extracts.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.persistence.crud import list_external_systems
from repowise.core.pipeline.incremental import refresh_external_systems
from tests.unit.persistence.helpers import insert_repo


def _diff(path: str):
    """Minimal stand-in for a FileDiff — the helper only reads ``.path``."""
    return SimpleNamespace(path=path, status="modified")


async def _names(session, repo_id: str) -> set[str]:
    return {s.name for s in await list_external_systems(session, repo_id)}


async def test_manifest_change_reextracts_and_reconciles(async_session, tmp_path):
    repo = await insert_repo(async_session)

    # v1: two runtime deps.
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "^18.0.0", "lodash": "^4.0.0"}}'
    )
    ran = await refresh_external_systems(async_session, repo.id, tmp_path, [_diff("package.json")])
    await async_session.commit()
    assert ran is True
    assert await _names(async_session, repo.id) == {"react", "lodash"}

    # v2: lodash dropped, axios added. A manifest changed, so re-extract runs;
    # the reconcile must add axios AND prune the now-removed lodash.
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "^18.0.0", "axios": "^1.0.0"}}'
    )
    ran = await refresh_external_systems(async_session, repo.id, tmp_path, [_diff("package.json")])
    await async_session.commit()
    assert ran is True
    assert await _names(async_session, repo.id) == {"react", "axios"}


async def test_non_manifest_change_is_a_noop(async_session, tmp_path):
    """A diff with no dependency manifest never re-extracts (the perf guard)."""
    repo = await insert_repo(async_session)

    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18.0.0"}}')
    assert await refresh_external_systems(async_session, repo.id, tmp_path, [_diff("package.json")])
    await async_session.commit()
    assert await _names(async_session, repo.id) == {"react"}

    # Edit the manifest on disk but hand the helper a source-only diff: the gate
    # must skip extraction entirely, leaving the init-time rows untouched.
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "^18.0.0", "vue": "^3.0.0"}}'
    )
    ran = await refresh_external_systems(async_session, repo.id, tmp_path, [_diff("src/index.ts")])
    await async_session.commit()
    assert ran is False
    assert await _names(async_session, repo.id) == {"react"}  # vue never picked up
