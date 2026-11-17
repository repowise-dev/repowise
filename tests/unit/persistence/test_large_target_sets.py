"""The recommendation reads are asked for every plan's files at once.

Finalizing the queues ranks every live plan in the repository, so the target
file set of these reads is the size of the repository. SQLite caps the bound
parameters of one statement (32766 in current builds, 999 before 3.32), so an
``IN`` list built from that set has to go out in pieces: one statement over
every path raises ``sqlite3.OperationalError: too many SQL variables`` and the
finalize, which is shared by the health persist, the queue rebuild after prune
and the idle-file re-score, fails with it.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud.analysis.coverage_map import (
    tests_covering_files as covering_files,
)
from repowise.core.persistence.crud.analysis.function_facts import (
    tests_in_files as load_tests_in_files,
)
from repowise.core.persistence.crud.analysis.refactoring_recommendations import _symbol_spans

# Above the largest per-statement parameter cap SQLite can be built with.
PATHS = [f"src/module_{i}/file_{i}.py" for i in range(33_000)]


@pytest.mark.parametrize(
    "read",
    [
        lambda session: _symbol_spans(session, "repo-1", PATHS),
        lambda session: load_tests_in_files(session, "repo-1", PATHS),
        lambda session: covering_files(session, "repo-1", set(PATHS)),
    ],
    ids=["symbol_spans", "tests_in_files", "tests_covering_files"],
)
async def test_a_target_set_larger_than_the_parameter_cap_is_read(async_session, read):
    assert await read(async_session) == {}


async def test_an_update_over_more_files_than_the_parameter_cap_persists(async_session):
    """The health persist, the plan finalize and the read snapshots of #4401.

    The cap is lowered to 600 on the test connection, so 700 files go past it
    the way a 9k-file repository goes past SQLite's 32766.
    """
    import sqlite3
    from types import SimpleNamespace

    from sqlalchemy import func, select

    from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
    from repowise.core.persistence.crud.analysis.actions import write_read_snapshots
    from repowise.core.persistence.models import (
        HealthFileMetric,
        HealthFinding,
    )
    from repowise.core.persistence.models import (
        RefactoringSuggestion as SuggestionRow,
    )
    from repowise.core.pipeline.incremental import persist_partial_health
    from tests.unit.persistence.helpers import insert_repo

    raw = await (await async_session.connection()).get_raw_connection()
    raw.driver_connection._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 600)
    repo = await insert_repo(async_session)
    paths = [f"src/file_{i}.py" for i in range(700)]
    report = SimpleNamespace(
        authoritative_paths=set(paths),
        performance_authoritative_paths=set(),
        performance_plan_policy=None,
        metrics=[{"file_path": p, "score": 6.0, "nloc": 120} for p in paths],
        findings=[
            {
                "file_path": p,
                "biomarker_type": "low_cohesion",
                "severity": "medium",
                "function_name": "Foo",
                "line_start": 1,
                "line_end": 80,
                "health_impact": 1.5,
                "reason": "low cohesion",
            }
            for p in paths
        ],
        refactoring_suggestions=[
            RefactoringSuggestion(
                refactoring_type="extract_class",
                file_path=p,
                target_symbol="Foo",
                line_start=1,
                line_end=80,
                plan={"groups": [{"name": "Store", "methods": ["m1", "m2"], "fields": ["a"]}]},
                evidence={"lcom4": 2, "method_count": 6, "field_count": 2, "wmc": 9},
                impact_delta=2.4,
                effort_bucket="M",
                blast_radius={"dependents_count": 3},
                confidence="high",
                source_biomarker="low_cohesion",
            )
            for p in paths
        ],
        function_blame_rows=[],
    )

    await persist_partial_health(async_session, repo.id, report)
    # Twice: the second pass replaces and reconciles stored rows over every path.
    await persist_partial_health(async_session, repo.id, report)
    assert await write_read_snapshots(async_session, repo.id) == []

    for model in (HealthFileMetric, HealthFinding, SuggestionRow):
        count = await async_session.scalar(
            select(func.count()).select_from(model).where(model.repository_id == repo.id)
        )
        assert count == len(paths), model.__name__
