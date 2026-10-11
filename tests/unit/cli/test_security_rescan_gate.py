"""The gate that decides when `repowise update` rescans security findings
in full (#3072), and the full rescan itself.

Mirrors ``test_health_rescore_gate.py``'s ``TestHealthAnalyzerChanged``: a
scanner version bump is the only trigger here, there is no time cadence,
since security findings do not decay the way health scores do.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from repowise.cli.commands.update_cmd.persistence import (
    run_full_security_rescan,
    security_scanner_changed,
)
from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION


class TestSecurityScannerChanged:
    def test_absent_stamp_is_not_a_change(self):
        """A legacy state file must not read as drift (mirrors health's rule)."""
        assert security_scanner_changed({}) is False

    def test_matching_stamp_is_not_a_change(self):
        assert (
            security_scanner_changed({"security_scanner_version": SECURITY_SCANNER_VERSION})
            is False
        )

    @pytest.mark.parametrize(
        "stored", [SECURITY_SCANNER_VERSION - 1, SECURITY_SCANNER_VERSION + 1]
    )
    def test_any_different_stamp_is_a_change(self, stored):
        """Not `<`: a downgrade also leaves rows this scanner did not write."""
        assert security_scanner_changed({"security_scanner_version": stored}) is True


async def _rows(repo_path: Path) -> list[tuple]:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import create_engine, create_session_factory, get_session

    engine = create_engine(get_db_url_for_repo(repo_path))
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        res = await session.execute(
            text(
                "SELECT file_path, kind, line_number, commit_sha FROM security_findings "
                "ORDER BY file_path, line_number, kind"
            )
        )
        rows = [tuple(r) for r in res.fetchall()]
    await engine.dispose()
    return rows


async def _seed_repo_with_a_stale_row(tmp_path: Path, *, with_history_row: bool) -> None:
    """A tiny repo with one file the current scanner has no finding for, plus
    a row as if an older scanner version wrote it there (the issue's repro)."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )
    from repowise.core.persistence.models import Repository

    (tmp_path / ".repowise").mkdir(exist_ok=True)
    (tmp_path / "a.py").write_text("def b():\n    return 2\n", encoding="utf-8")

    from repowise.cli.helpers import get_db_url_for_repo

    engine = create_engine(get_db_url_for_repo(tmp_path))
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        session.add(Repository(id="repo-1", name="t", local_path=str(tmp_path)))
        await session.flush()
        await session.execute(
            text(
                "INSERT INTO security_findings "
                "(repository_id, file_path, kind, severity, snippet, line_number, "
                "commit_sha, detected_at) "
                "VALUES ('repo-1', 'a.py', 'hardcoded_secret', 'high', 'x', 1, "
                "'', datetime('now'))"
            )
        )
        if with_history_row:
            await session.execute(
                text(
                    "INSERT INTO security_findings "
                    "(repository_id, file_path, kind, severity, snippet, line_number, "
                    "commit_sha, detected_at) "
                    "VALUES ('repo-1', 'a.py', 'hardcoded_secret', 'high', 'x', 1, "
                    "'deadbeef', datetime('now'))"
                )
            )
    await engine.dispose()


class TestFullSecurityRescan:
    def test_a_stale_working_tree_row_is_replaced(self, tmp_path: Path) -> None:
        import asyncio

        asyncio.run(_seed_repo_with_a_stale_row(tmp_path, with_history_row=False))
        before = asyncio.run(_rows(tmp_path))
        assert ("a.py", "hardcoded_secret", 1, "") in before

        assert run_full_security_rescan(tmp_path, []) is True

        after = asyncio.run(_rows(tmp_path))
        # The stale row named a finding the current scanner does not make for
        # this file's actual content; the rescan must not leave it standing.
        assert ("a.py", "hardcoded_secret", 1, "") not in after

    def test_a_history_row_survives_the_full_rescan(self, tmp_path: Path) -> None:
        import asyncio

        asyncio.run(_seed_repo_with_a_stale_row(tmp_path, with_history_row=True))
        assert run_full_security_rescan(tmp_path, []) is True

        after = asyncio.run(_rows(tmp_path))
        # replace_findings only ever touches commit_sha='' (working-tree) rows.
        assert ("a.py", "hardcoded_secret", 1, "deadbeef") in after
