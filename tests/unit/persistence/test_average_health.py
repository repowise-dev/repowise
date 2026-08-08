"""``get_average_health`` — the badge's one number, without the dataset.

The public badge endpoints render a single float and were reading the whole
health dataset to get it. The narrow read has to agree with
``get_health_summary`` exactly, or the badge in a README disagrees with the
dashboard it links to.
"""

from __future__ import annotations

from repowise.core.persistence.crud import (
    get_average_health,
    get_health_summary,
    save_health_metrics,
    upsert_repository,
)


def _metric(path: str, score: float, nloc: int) -> dict:
    return {
        "file_path": path,
        "score": score,
        "max_ccn": 1,
        "max_nesting": 1,
        "nloc": nloc,
        "has_test_file": False,
        "module": path.split("/")[0],
    }


async def test_matches_the_summary_and_is_nloc_weighted(async_session, tmp_path) -> None:
    """Deliberately lopsided NLOC: a plain mean would give 5.5, not 1.9."""
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await save_health_metrics(
        async_session,
        repo.id,
        [_metric("src/big.py", 1.0, 900), _metric("src/small.py", 10.0, 100)],
    )

    avg = await get_average_health(async_session, repo.id)

    assert avg == 1.9  # (1.0*900 + 10.0*100) / 1000
    assert avg == (await get_health_summary(async_session, repo.id))["average_health"]


async def test_zero_nloc_rows_fall_back_to_a_plain_mean(async_session, tmp_path) -> None:
    """``max(nloc, 1)`` floors the weight, matching the summary's arithmetic."""
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await save_health_metrics(
        async_session, repo.id, [_metric("a.py", 2.0, 0), _metric("b.py", 8.0, 0)]
    )

    assert await get_average_health(async_session, repo.id) == 5.0
    assert (
        await get_average_health(async_session, repo.id)
        == (await get_health_summary(async_session, repo.id))["average_health"]
    )


async def test_honors_the_exclusion_spec(async_session, tmp_path) -> None:
    """The reason this is a narrow select reduced in Python, not a SQL ``AVG``.

    Exclusion is a compiled pathspec, so SQL cannot apply it. An ``AVG()`` would
    have averaged the excluded rows in and reported a different number than the
    dashboard — silently, and only on repos that configure excludes.
    """
    repo = await upsert_repository(
        async_session,
        name="repo",
        local_path=str(tmp_path),
        settings={"exclude_patterns": ["generated/"]},
    )
    await save_health_metrics(
        async_session,
        repo.id,
        [_metric("src/app.py", 8.0, 100), _metric("generated/pb.py", 1.0, 900)],
    )

    # Unfiltered this would be 1.7; the excluded file must not count at all.
    assert await get_average_health(async_session, repo.id) == 8.0
    assert (
        await get_average_health(async_session, repo.id)
        == (await get_health_summary(async_session, repo.id))["average_health"]
    )


async def test_unmeasured_repo_reports_none_rather_than_a_perfect_score(
    async_session, tmp_path
) -> None:
    """No rows is "not measured", not 10.0.

    ``get_health_summary`` returns 10.0 here, which the badge endpoint has
    always rendered and still does — it maps ``None`` back to 10.0 itself. The
    crud helper stays honest so a new caller is not handed a perfect score for
    a repo nobody has analysed.
    """
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))

    assert await get_average_health(async_session, repo.id) is None
    assert (await get_health_summary(async_session, repo.id))["average_health"] == 10.0
