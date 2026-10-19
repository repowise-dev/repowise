"""An analyzer or parser change must get past the "already up to date" return.

With no new commit, ``head == base_ref`` is the normal state of a checkout, so
the version stamps are the only thing that can tell ``update`` that every stored
score or graph edge was written by an older build. A scanner change is rescanned
on that return itself, since the rescan does its own full parse.
"""

from __future__ import annotations

import pytest

from repowise.cli.commands.update_cmd import command as upd_cmd
from repowise.cli.helpers import load_state, save_state
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION

from .test_update_up_to_date_lock import _indexed_repo, _invoke_update

UP_TO_DATE = "Already up to date"


def test_current_versions_stay_up_to_date(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = _indexed_repo(tmp_path)
    state = load_state(repo)
    save_state(
        repo,
        {
            **state,
            "health_analyzer_version": HEALTH_ANALYZER_VERSION,
            "security_scanner_version": SECURITY_SCANNER_VERSION,
        },
    )
    rescored: list[int] = []
    monkeypatch.setattr(upd_cmd, "parser_changed", lambda _p: False)
    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.persistence.run_decay_health_rescore",
        lambda *a, **k: rescored.append(1) or True,
    )
    rescanned: list[int] = []
    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.persistence.run_full_security_rescan",
        lambda *a, **k: rescanned.append(1) or True,
    )

    out = _invoke_update(repo)

    assert UP_TO_DATE in out
    assert not rescored
    assert not rescanned


def test_stale_analyzer_version_rescores_an_unchanged_checkout(tmp_path) -> None:
    repo, c2 = _indexed_repo(tmp_path)
    save_state(
        repo, {**load_state(repo), "health_analyzer_version": HEALTH_ANALYZER_VERSION - 1}
    )

    out = _invoke_update(repo)

    assert UP_TO_DATE not in out
    assert "Health analyzer changed" in out
    state = load_state(repo)
    assert state["health_analyzer_version"] == HEALTH_ANALYZER_VERSION
    assert state["last_sync_commit"] == c2
    # Stamped, so the next run is quiet again.
    assert UP_TO_DATE in _invoke_update(repo)


def test_parser_change_reparses_an_unchanged_checkout(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = _indexed_repo(tmp_path)
    save_state(repo, {**load_state(repo), "health_analyzer_version": HEALTH_ANALYZER_VERSION})
    monkeypatch.setattr(upd_cmd, "parser_changed", lambda _p: True)
    rescored: list[int] = []
    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.persistence.run_decay_health_rescore",
        lambda *a, **k: rescored.append(1) or True,
    )

    out = _invoke_update(repo)

    assert UP_TO_DATE not in out
    assert "Parser changed" in out
    assert rescored


def test_missing_parser_stamp_reparses_an_unchanged_checkout(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An index written before init stamped the parser re-parses once, then is quiet."""
    import asyncio

    from repowise.core.persistence import create_engine
    from repowise.core.persistence.models import Repository

    async def _all_scanned(*_a):
        return []

    # Isolate the stamp from the test-file scan, the gate's other trigger.
    monkeypatch.setattr(
        "repowise.core.analysis.test_reachability.unscanned_test_files", _all_scanned
    )
    from repowise.core.pipeline.full_index import index_repo_full

    from .test_update_reconciles_stale_pages import CURRENT_GIT_HISTORY, _repo_at_head

    # Source with edges, so the unstamped store has something to reconcile.
    repo, c2 = _repo_at_head(tmp_path)
    asyncio.run(index_repo_full(repo))
    save_state(
        repo,
        {
            "last_sync_commit": c2,
            "last_docs_commit": c2,
            "docs_enabled": False,
            "health_analyzer_version": HEALTH_ANALYZER_VERSION,
            **CURRENT_GIT_HISTORY,
        },
    )
    assert not upd_cmd.parser_changed(repo)

    async def _unstamp() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
        async with engine.begin() as conn:
            await conn.execute(
                Repository.__table__.update().values(graph_edges_parser_fingerprint=None)
            )
        await engine.dispose()

    asyncio.run(_unstamp())
    assert upd_cmd.parser_changed(repo)

    out = _invoke_update(repo)

    assert UP_TO_DATE not in out
    assert "Parser changed" in out
    assert not upd_cmd.parser_changed(repo)
    assert load_state(repo)["last_sync_commit"] == c2
    assert UP_TO_DATE in _invoke_update(repo)


@pytest.mark.parametrize("stored", [None, SECURITY_SCANNER_VERSION - 1])
def test_stale_scanner_rescans_an_unchanged_checkout_without_a_reparse(
    tmp_path, monkeypatch: pytest.MonkeyPatch, stored
) -> None:
    """None is an index from 0.55.0 or earlier, written before the stamp."""
    repo, c2 = _indexed_repo(tmp_path)
    state = {**load_state(repo), "health_analyzer_version": HEALTH_ANALYZER_VERSION}
    state.pop("security_scanner_version", None)
    if stored is not None:
        state["security_scanner_version"] = stored
    save_state(repo, state)
    monkeypatch.setattr(upd_cmd, "parser_changed", lambda _p: False)

    def _no_reparse(*a, **k):
        raise AssertionError("a scanner-only change must not re-parse for the graph")

    monkeypatch.setattr(upd_cmd, "_rebuild_graph_and_git", _no_reparse)
    rescanned: list[int] = []
    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.persistence.run_full_security_rescan",
        lambda *a, **k: rescanned.append(1) or True,
    )

    out = _invoke_update(repo)

    assert "Security scanner changed" in out
    assert rescanned == [1]
    state = load_state(repo)
    assert state["security_scanner_version"] == SECURITY_SCANNER_VERSION
    assert state["last_sync_commit"] == c2
    # Stamped, so the next run is quiet again.
    assert UP_TO_DATE in _invoke_update(repo)
    assert rescanned == [1]


def test_failed_rescan_leaves_the_stamp_for_a_retry(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = _indexed_repo(tmp_path)
    save_state(repo, {**load_state(repo), "health_analyzer_version": HEALTH_ANALYZER_VERSION})
    monkeypatch.setattr(upd_cmd, "parser_changed", lambda _p: False)
    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.persistence.run_full_security_rescan",
        lambda *a, **k: False,
    )

    _invoke_update(repo)

    assert "security_scanner_version" not in load_state(repo)
