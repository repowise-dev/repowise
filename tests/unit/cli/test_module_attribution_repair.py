"""``repowise update`` repairs module attribution, including on a quiet repo.

The trigger placement is the design decision here. A ``HEALTH_ANALYZER_VERSION``
bump would also reach these rows, but it buys a directory label at the price of
a full health re-score — and the re-score gate is only consulted once an update
reaches the incremental path, so a repo with no new commits returns at "already
up to date" and never picks the correction up. The repair runs before that
return and costs one pruned directory walk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.update_cmd import persistence


def test_it_scans_through_the_traverser_and_writes_what_changed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Roots come from the traverser, not a bare walk.

    A bare walk descends into nested checkouts and vendored trees. On this repo
    that took the scanned-root count from 9 to 512 and the scan from 2.3s to
    minutes, on a repo that physically contains sixteen worktrees.
    """
    seen: dict = {}

    class _Traverser:
        def __init__(self, repo_path):
            seen["repo_path"] = repo_path

        def package_root_dirs(self):
            return {"services/api"}

    async def _fake_backfill(_session, _repo_id, package_roots):
        seen["roots"] = package_roots
        return 4

    import repowise.core.ingestion.traverser as traverser_mod
    import repowise.core.persistence.crud as crud_mod

    monkeypatch.setattr(traverser_mod, "FileTraverser", _Traverser)
    monkeypatch.setattr(crud_mod, "backfill_module_attribution", _fake_backfill)

    assert persistence._repair_module_attribution(tmp_path) == 4
    assert seen["roots"] == {"services/api"}
    assert Path(seen["repo_path"]) == tmp_path


def test_a_failure_never_fails_the_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreadable tree or a missing index must not abort ``update``.

    Nothing downstream consumes the result, and the next update retries, so
    swallowing beats propagating.
    """
    import repowise.core.ingestion.traverser as traverser_mod

    def _boom(_repo_path):
        raise OSError("tree is gone")

    monkeypatch.setattr(traverser_mod, "FileTraverser", _boom)

    assert persistence._repair_module_attribution(tmp_path) == 0


def test_it_reports_only_when_it_changed_something(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """A no-op update must stay silent.

    It runs on every update including quiet ones, so a line per run would be
    noise on every repo that is already correct — which, censused, is every
    repo without a nested manifest.
    """

    class _Traverser:
        def __init__(self, repo_path):
            pass

        def package_root_dirs(self):
            return set()

    import repowise.core.ingestion.traverser as traverser_mod
    import repowise.core.persistence.crud as crud_mod

    monkeypatch.setattr(traverser_mod, "FileTraverser", _Traverser)

    async def _no_change(_s, _r, _roots):
        return 0

    monkeypatch.setattr(crud_mod, "backfill_module_attribution", _no_change)
    assert persistence._repair_module_attribution(tmp_path) == 0
    assert "Module attribution" not in capsys.readouterr().out

    async def _changed(_s, _r, _roots):
        return 7

    monkeypatch.setattr(crud_mod, "backfill_module_attribution", _changed)
    assert persistence._repair_module_attribution(tmp_path) == 7
    assert "Module attribution" in capsys.readouterr().out
