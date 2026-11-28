"""The commit a coverage ingest records itself against.

A report found on disk measures the tree as of the run that wrote it, which
can be commits behind the HEAD being indexed alongside it. The writers used to
stamp live HEAD unconditionally, so the forward drift read diffed from a commit
that did not exist when the suite ran and reported zero (#3901).
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from repowise.core.analysis.health.coverage import CoverageProvenance
from repowise.core.analysis.health.coverage.model import FileCoverage
from repowise.core.persistence.models import CoverageFile, CoverageIngest
from repowise.core.pipeline.persist import persist_analysis
from tests.unit.persistence.helpers import insert_repo

_PATH = "src/app.py"


def _git(cwd, *args: str, when: str | None = None) -> str:
    """Run git in *cwd*. *when* pins both dates, since ``rev-list --before``
    filters on the committer date and ``git commit --date`` only sets the
    author one."""
    env = None
    if when is not None:
        env = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env
    ).stdout


@pytest.fixture
def git_repo(tmp_path):
    """Two commits, the second dated an hour into the future.

    The future date is what makes the fixture deterministic: "now" then sits
    strictly between the two, so a report stamped now resolves to the first
    commit instead of whichever side of a real commit time the run lands on.
    """
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    (tmp_path / "src" / "app.py").write_text("x = 10\ny = 20\nz = 30\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "rewrite", when=later)
    return tmp_path


def _result(measured_at: datetime | None = None) -> SimpleNamespace:
    """A pipeline result carrying only what the analysis phase reads.

    *measured_at* is the report's own write time, which the discovery layer
    puts on the provenance; ``None`` means the writer never resolved one.
    Deliberately no ``repo_path``: that is the shape the resume checkpoint
    hands over, and the reason the tree has to come from the repository row.
    """
    return SimpleNamespace(
        dead_code_report=None,
        decision_report=None,
        health_report=SimpleNamespace(
            metrics=[
                {
                    "file_path": _PATH,
                    "score": 5.0,
                    "max_ccn": 3,
                    "max_nesting": 1,
                    "nloc": 40,
                    "duplication_pct": 0.0,
                    "has_test_file": False,
                    "line_coverage_pct": None,
                    "branch_coverage_pct": None,
                    "module": "src",
                }
            ],
            findings=[],
            refactoring_suggestions=[],
            performance_plan_policy=None,
            kpis={},
            coverage_files=[
                FileCoverage(
                    file_path=_PATH,
                    line_coverage_pct=100.0,
                    branch_coverage_pct=None,
                    covered_lines=[1, 2, 3],
                    total_coverable_lines=3,
                )
            ],
            coverage_format="lcov",
            coverage_provenance=CoverageProvenance(
                source_formats=("lcov",), measured_at=measured_at
            ),
            function_blame_rows=None,
        ),
    )


async def _ingest_stamps(session, repo_id: str) -> list[str | None]:
    rows = (
        (
            await session.execute(
                select(CoverageIngest).where(CoverageIngest.repository_id == repo_id)
            )
        )
        .scalars()
        .all()
    )
    assert rows, "the analysis phase must have written an ingest row"
    return [row.ingested_commit_sha for row in rows]


async def _file_stamps(session, repo_id: str) -> set[str | None]:
    rows = (
        (
            await session.execute(
                select(CoverageFile).where(CoverageFile.repository_id == repo_id)
            )
        )
        .scalars()
        .all()
    )
    assert rows, "the analysis phase must have written coverage rows"
    return {row.ingested_commit_sha for row in rows}


class TestMeasuredCommitStamp:
    async def test_a_report_older_than_head_is_stamped_where_it_measured(
        self, async_session, git_repo
    ):
        """The defect: an on-disk report was recorded at HEAD, so drift read zero.

        The ingest has to name the commit that headed when the suite ran, which
        is what lets ``decay_since`` diff the covered lines forward from there
        and see that all three of them have since moved.
        """
        head, first = _git(git_repo, "rev-list", "HEAD").strip().split()
        repo = await insert_repo(async_session, local_path=str(git_repo))
        await persist_analysis(_result(datetime.now(UTC)), async_session, repo.id)

        assert await _ingest_stamps(async_session, repo.id) == [first]
        assert await _file_stamps(async_session, repo.id) == {first}
        assert head != first

    async def test_a_report_written_since_the_last_commit_still_stamps_head(
        self, async_session, git_repo
    ):
        """A fresh ``coverage lcov`` straight before indexing keeps today's answer.

        The commit that headed at its time is HEAD, so the fix costs nothing for
        the case every writer already got right; this test is what stops a later
        tightening from pulling fresh reports back to their parent.
        """
        head = _git(git_repo, "rev-parse", "HEAD").strip()
        repo = await insert_repo(async_session, local_path=str(git_repo))
        await persist_analysis(
            _result(datetime.now(UTC) + timedelta(hours=2)), async_session, repo.id
        )
        assert await _ingest_stamps(async_session, repo.id) == [head]

    async def test_a_writer_that_does_not_know_the_time_stamps_head(
        self, async_session, git_repo
    ):
        """Unknown is not "as old as the filesystem": a report dated now, and no
        date at all, both stay at HEAD."""
        head = _git(git_repo, "rev-parse", "HEAD").strip()
        repo = await insert_repo(async_session, local_path=str(git_repo))
        await persist_analysis(_result(None), async_session, repo.id)
        assert await _ingest_stamps(async_session, repo.id) == [head]

    async def test_the_stamp_needs_no_repo_path_on_the_result(self, async_session, git_repo):
        """The tree comes from the repository row, because the result has none.

        Reading the path off the pipeline result resolved nothing on the resume
        path, so one run wrote two ingests of the same measurement — one at HEAD,
        one at the report's commit — and split the trend across them.
        """
        first = _git(git_repo, "rev-parse", "HEAD~1").strip()
        repo = await insert_repo(async_session, local_path=str(git_repo))
        assert not hasattr(_result(), "repo_path")
        await persist_analysis(_result(datetime.now(UTC)), async_session, repo.id)
        assert await _ingest_stamps(async_session, repo.id) == [first]
