"""Unit tests for c4_builder.actors — actor derivation from entry points."""

from __future__ import annotations

import pytest

from repowise.server.services.c4_builder.actors import (
    classify_entry_point,
    derive_actors,
)


@pytest.mark.parametrize(
    "path,kind",
    [
        ("packages/cli/src/repowise/cli/main.py", "cli"),
        ("src/app/__main__.py", "cli"),
        ("manage.py", "cli"),
        ("packages/server/src/repowise/server/app.py", "api"),
        ("services/gateway/asgi.py", "api"),
        ("app/api/routes.py", "api"),
        ("jobs/cron/nightly.py", "scheduler"),
        ("src/worker/tasks.py", "scheduler"),
        ("scripts/kg_validate/run.py", "developer"),
        ("tools/gen.py", "developer"),
        ("packages/types/src/index.ts", None),
        ("README.md", None),
    ],
)
def test_classify_entry_point(path, kind):
    assert classify_entry_point(path) == kind


def test_derive_actors_dedupes_and_orders():
    actors = derive_actors(
        [
            "packages/cli/src/repowise/cli/main.py",
            "packages/server/src/repowise/server/app.py",
            "scripts/kg_validate/run.py",
            "another/cli/thing.py",  # duplicate cli kind
        ]
    )
    assert [a.kind for a in actors] == ["cli", "api", "developer"]
    assert [a.id for a in actors] == ["person:cli", "person:api", "person:developer"]
    assert all(a.name and a.verb for a in actors)


def test_derive_actors_falls_back_to_generic_user():
    actors = derive_actors([])
    assert [a.kind for a in actors] == ["user"]
    assert actors[0].name == "User"


def test_derive_actors_ignores_unclassifiable_only():
    actors = derive_actors(["packages/types/src/index.ts", "README.md"])
    assert [a.kind for a in actors] == ["user"]
