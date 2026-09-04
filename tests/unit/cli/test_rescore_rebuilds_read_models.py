"""What the update command's full health re-score must leave behind.

The re-score deletes and re-inserts every finding in the repository. The
performance and refactoring queues are materialized *from* those findings, so
a re-score that does not rebuild them leaves them serving rows derived from a
finding set that no longer exists. This drives the real re-score entry point
and pins that it reconciles them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import pytest
from sqlalchemy import select

from repowise.cli.commands.update_cmd.persistence import _rescore_health_from_db
from repowise.cli.helpers import get_db_url_for_repo
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_repository,
)
from repowise.core.persistence.models import (
    HealthFileMetric,
    PerformanceOpportunity,
    PerformanceSummary,
)


class _EmptyGraphBuilder:
    """The re-score reuses the update's builder; none of this run needs one."""

    def graph(self):
        return nx.DiGraph()

    def pagerank(self):
        return {}

    betweenness_centrality = pagerank
    symbol_pagerank = pagerank
    symbol_betweenness_centrality = pagerank
    community_detection = pagerank
    symbol_communities = pagerank

    def community_info(self):
        return {}


_SCORED = "src/app.py"
_SOURCE = "def run(items):\n    return [i for i in items if i]\n"


def _parsed_file(repo_path: Path) -> SimpleNamespace:
    """The re-score scores the caller's parsed files; one real one is enough."""
    return SimpleNamespace(
        file_info=SimpleNamespace(
            path=_SCORED,
            language="python",
            abs_path=str(repo_path / _SCORED),
            is_test=False,
        ),
        symbols=[],
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("parity\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / _SCORED).write_text(_SOURCE, encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["add", "."],
        ["commit", "-q", "-m", "init"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


async def _seed_orphan_opportunity(repo_path: Path) -> tuple[str, str]:
    """An open queue row with no finding behind it, as a re-score would leave."""
    engine = create_engine(get_db_url_for_repo(repo_path))
    await init_db(engine)
    factory = create_session_factory(engine)
    async with get_session(factory) as session:
        repo = await upsert_repository(
            session, name=repo_path.name, local_path=str(repo_path)
        )
        session.add(
            PerformanceOpportunity(
                id="orphan-row",
                repository_id=repo.id,
                opportunity_id="perf2_orphaned_cause",
                performance_model_version=2,
                status="open",
                rank_position=0,
                rank_score=1.0,
                file_path="src/app.py",
                observations_total=1,
                affected_call_sites_total=1,
                affected_files_total=1,
                analyzed_commit="0" * 40,
            )
        )
        await session.commit()
        repo_id = repo.id
    await engine.dispose()
    return repo_id, "perf2_orphaned_cause"


async def _read_back(repo_path: Path, repo_id: str):
    engine = create_engine(get_db_url_for_repo(repo_path))
    factory = create_session_factory(engine)
    async with get_session(factory) as session:
        rows = (
            (
                await session.execute(
                    select(PerformanceOpportunity).where(
                        PerformanceOpportunity.repository_id == repo_id
                    )
                )
            )
            .scalars()
            .all()
        )
        statuses = {row.opportunity_id: row.status for row in rows}
        summary = await session.get(PerformanceSummary, repo_id)
        summary_commit = summary.analyzed_commit if summary else None
        metrics = {
            row.file_path: row.analyzed_commit
            for row in (
                (
                    await session.execute(
                        select(HealthFileMetric).where(
                            HealthFileMetric.repository_id == repo_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        }
    await engine.dispose()
    return statuses, summary_commit, metrics


class TestRescoreRebuildsTheReadModels:
    async def test_a_cause_with_no_surviving_finding_is_resolved(self, git_repo: Path):
        repo_id, opportunity_id = await _seed_orphan_opportunity(git_repo)

        await _rescore_health_from_db(
            git_repo, _EmptyGraphBuilder(), [_parsed_file(git_repo)], []
        )

        statuses, _, _ = await _read_back(git_repo, repo_id)
        assert statuses[opportunity_id] == "resolved"

    async def test_it_stamps_the_commit_it_scored_against(self, git_repo: Path):
        repo_id, _ = await _seed_orphan_opportunity(git_repo)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        await _rescore_health_from_db(
            git_repo, _EmptyGraphBuilder(), [_parsed_file(git_repo)], []
        )

        _, summary_commit, metrics = await _read_back(git_repo, repo_id)
        assert summary_commit == head
        # The column the index-freshness signal is read from.
        assert metrics[_SCORED] == head
