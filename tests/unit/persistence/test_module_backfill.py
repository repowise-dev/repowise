"""Correcting ``module`` must never cost an index run.

``module`` is a persisted column, so a change to how it is derived would
normally only reach stored rows when something rewrote them — a full health
re-score at best, a re-index at worst. Neither is an acceptable price for a
directory label, and neither is necessary: the label is a pure function of
``(file_path, package_roots)``, so it can be recomputed in place from a
directory scan alone. No parse, no embedding, no model.
"""

from __future__ import annotations

from repowise.core.persistence.crud import (
    backfill_module_attribution,
    get_health_metrics,
    save_health_metrics,
    upsert_repository,
)

ROOTS = {"services/api", "services/api/internal/tools", "libs/billing"}


def _metric(path: str, module: str | None) -> dict:
    return {
        "file_path": path,
        "score": 5.0,
        "max_ccn": 1,
        "max_nesting": 1,
        "nloc": 10,
        "has_test_file": False,
        "module": module,
    }


async def _modules(session, repo_id) -> dict[str, str | None]:
    rows = await get_health_metrics(session, repo_id)
    return {r.file_path: r.module for r in rows}


async def test_it_corrects_rows_no_rescore_would_have_touched(async_session, tmp_path) -> None:
    """The rows a Go or Maven monorepo has today: bucketed by top-level dir.

    Nothing rewrites these until a full re-score fires, which is gated on a
    7-day decay timer. This is the mechanism that makes the correction land
    without waiting for it, and without an index run.
    """
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await save_health_metrics(
        async_session,
        repo.id,
        [
            _metric("services/api/main.go", "services"),
            _metric("services/api/internal/tools/t.go", "services"),
            _metric("libs/billing/src/Main.java", "libs"),
            _metric("scripts/deploy.py", "scripts"),
        ],
    )

    changed = await backfill_module_attribution(async_session, repo.id, ROOTS)

    assert changed == 3
    assert await _modules(async_session, repo.id) == {
        "services/api/main.go": "services/api",
        # Deepest package wins over the one containing it.
        "services/api/internal/tools/t.go": "services/api/internal/tools",
        "libs/billing/src/Main.java": "libs/billing",
        # No enclosing package: the top-level fallback is already right.
        "scripts/deploy.py": "scripts",
    }


async def test_running_it_again_changes_nothing(async_session, tmp_path) -> None:
    """Idempotent, because it runs on every update — including quiet ones.

    A second pass reporting work would mean it is fighting whatever wrote the
    rows rather than agreeing with it.
    """
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await save_health_metrics(async_session, repo.id, [_metric("services/api/main.go", "services")])

    assert await backfill_module_attribution(async_session, repo.id, ROOTS) == 1
    assert await backfill_module_attribution(async_session, repo.id, ROOTS) == 0
    assert await backfill_module_attribution(async_session, repo.id, ROOTS) == 0


async def test_it_agrees_with_what_the_indexer_writes(async_session, tmp_path) -> None:
    """The backfill and the analyzer must not disagree about the repo layout.

    Both call ``module_for`` over the same disk-scanned roots. If a full index
    wrote a different value, the next update would flip it back and the two
    would alternate forever — so this pins that they are the same function.
    """
    from repowise.core.ingestion.package_roots import module_for

    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    paths = [
        "services/api/main.go",
        "services/api/internal/tools/t.go",
        "libs/billing/src/Main.java",
        "scripts/deploy.py",
        "README.md",
    ]
    # Seed with exactly what the analyzer would compute, then backfill.
    await save_health_metrics(
        async_session, repo.id, [_metric(p, module_for(p, ROOTS)) for p in paths]
    )

    assert await backfill_module_attribution(async_session, repo.id, ROOTS) == 0


async def test_a_root_level_file_is_cleared_to_none(async_session, tmp_path) -> None:
    """``None``, not ``""`` — the rollup must not grow a phantom empty bucket."""
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await save_health_metrics(async_session, repo.id, [_metric("README.md", "README")])

    assert await backfill_module_attribution(async_session, repo.id, ROOTS) == 1
    assert (await _modules(async_session, repo.id))["README.md"] is None


async def test_it_clears_the_community_labels_left_by_old_rows(async_session, tmp_path) -> None:
    """The namespace this replaced, including its dedupe suffix.

    ``module`` used to be ``community_label or top_level_dir``, so a row could
    name a directory the file does not live in — measured at 1,355 of 3,263
    files on this repo — and could carry a ``" (N)"`` disambiguation suffix.
    Those rows only correct themselves if something rewrites them.
    """
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await save_health_metrics(
        async_session,
        repo.id,
        [
            _metric("services/api/main.go", "tests/unit"),
            _metric("libs/billing/src/Main.java", "repowise/ingestion (40)"),
        ],
    )

    assert await backfill_module_attribution(async_session, repo.id, ROOTS) == 2
    assert await _modules(async_session, repo.id) == {
        "services/api/main.go": "services/api",
        "libs/billing/src/Main.java": "libs/billing",
    }


async def test_no_package_roots_leaves_a_correct_repo_alone(async_session, tmp_path) -> None:
    """The two sibling repos in this workspace: no nested manifests, no change.

    Censused on the real indexes of a FastAPI backend (674 rows) and a Next.js
    frontend (1,058 rows): zero rows move. A backfill that rewrote them would
    be a regression, not a fix.
    """
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await save_health_metrics(
        async_session,
        repo.id,
        [
            _metric("app/routers/files.py", "app"),
            _metric("src/components/x.tsx", "src"),
            _metric("tests/unit/a.py", "tests"),
        ],
    )

    assert await backfill_module_attribution(async_session, repo.id, set()) == 0
