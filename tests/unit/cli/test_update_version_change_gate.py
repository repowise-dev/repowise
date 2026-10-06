"""An analyzer or parser change must get past the "already up to date" return.

With no new commit, ``head == base_ref`` is the normal state of a checkout, so
the version stamps are the only thing that can tell ``update`` that every stored
score or graph edge was written by an older build.
"""

from __future__ import annotations

import pytest

from repowise.cli.commands.update_cmd import command as upd_cmd
from repowise.cli.helpers import load_state, save_state
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION

from .test_update_up_to_date_lock import _indexed_repo, _invoke_update

UP_TO_DATE = "Already up to date"


def test_current_versions_stay_up_to_date(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = _indexed_repo(tmp_path)
    state = load_state(repo)
    save_state(repo, {**state, "health_analyzer_version": HEALTH_ANALYZER_VERSION})
    rescored: list[int] = []
    monkeypatch.setattr(upd_cmd, "parser_changed", lambda _p: False)
    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.persistence.run_decay_health_rescore",
        lambda *a, **k: rescored.append(1) or True,
    )

    out = _invoke_update(repo)

    assert UP_TO_DATE in out
    assert not rescored


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
