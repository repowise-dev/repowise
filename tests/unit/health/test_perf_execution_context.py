"""Schema migrations run at deploy time, so they are tooling, not production.

A migration's loop over rows is not a request path, and classing it
``production`` ranked it beside the code users wait on. A bare ``versions/``
directory stays production: API version packages use the same name.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.perf.causal import execution_context


@pytest.mark.parametrize(
    "path",
    [
        "app/migrations/0001_initial.py",  # Django
        "blog/migrations/0012_backfill.py",
        "migrations/versions/3f2a_add_index.py",  # Flask-Migrate
        "alembic/versions/3f2a_add_index.py",  # Alembic
        "src/db/alembic/versions/3f2a_add_index.py",
        "db/migrate/20240101000000_create_users.rb",  # Rails
        "src/Data/Migrations/20240101_Init.cs",  # EF Core
        "src\\Data\\Migrations\\20240101_Init.cs",
    ],
)
def test_migrations_are_tooling(path: str) -> None:
    assert execution_context(path) == "tooling"


@pytest.mark.parametrize(
    "path",
    [
        "app/versions/api.py",
        "api/versions/v2/handlers.py",
        "app/db/migrate.py",  # a module named migrate, not the Rails directory
        "app/migrate/runner.py",
        "app/alembic_utils.py",
        "app/migrations_helper.py",
    ],
)
def test_lookalikes_stay_production(path: str) -> None:
    assert execution_context(path) == "production"
