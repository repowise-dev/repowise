"""Values a Python function builds as an eager ``setdefault`` default: read on the walk,
stored per function, and served to the next step beside the static findings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity.walker import walk_file

# The shape of a CSV inserter's column-type tracker: one tracker per cell.
_PY = b"""
class TypeTracker:
    def wrap(self, iterator):
        for row in iterator:
            for key, value in row.items():
                tracker = self.trackers.setdefault(key, ValueTracker())
                tracker.evaluate(value)
            yield row


def group(rows):
    out = {}
    for row in rows:
        out.setdefault(row.key, set()).add(row)
        out.setdefault(row.name, [])
        out.setdefault(row.id, row.value)
        out.setdefault(row.id)
        out.setdefault(row.id, *row.rest)
        out.setdefault(row.id, default=make())
    return out


def outer(items):
    def inner(d, k):
        return d.setdefault(k, models.Counter(k))

    lazy = lambda d, k: d.setdefault(k, Counter())
    return inner, lazy


def twice(d, rows):
    first = Tracker()
    d.setdefault(rows[0], Tracker())
    d.setdefault(rows[1], (Box()))
    return (d.setdefault(r, Later()) for r in rows), first


def defined(d, k, arg=cache.setdefault(1, Once())):
    return arg


def hidden(d, k):
    def g(x=Seed()):
        return x

    (Pair)()
    d.setdefault(k, Seed())
    d.setdefault(k, Pair())
    return d.setdefault(k, (  # a comment
        Note()
    ))
"""


def _eager(source: bytes = _PY) -> dict[str, tuple]:
    return {fc.name: fc.eager_defaults for fc in walk_file("t.py", "python", source).functions}


def test_a_call_as_the_setdefault_default_is_read_per_function() -> None:
    eager = _eager()
    assert eager["wrap"] == (("ValueTracker", 6),)
    # A literal, a plain value, one argument, a splat or a keyword is no eager build.
    assert eager["group"] == (("set", 14),)
    # A nested function owns its own; a lambda's runs on the lambda's calls.
    assert eager["inner"] == (("models.Counter", 25),)
    assert eager["outer"] == ()
    # A name the function also calls elsewhere is no fact (the frame cannot tell
    # the two apart); grouping parentheses are read through; a generator
    # expression's default runs later, a parameter default once.
    assert eager["twice"] == (("Box", 34),)
    assert eager["defined"] == ()
    # A nested definition's parameter default runs here too, and so does a call
    # through grouping parentheses: both count. A comment inside them is read through.
    assert eager["hidden"] == (("Note", 50),)


def test_other_languages_and_files_without_setdefault_read_nothing() -> None:
    assert _eager(b"def f(d, k):\n    return d.get(k, Tracker())\n") == {"f": ()}
    js = b"function f(d, k) { return d.setdefault(k, new Tracker()); }"
    assert all(fc.eager_defaults == () for fc in walk_file("t.js", "javascript", js).functions)


@pytest.fixture
async def store(tmp_path: Path):
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
            await session.commit()
            yield session, repo.id
    finally:
        await engine.dispose()


async def test_stored_eager_defaults_reach_the_next_step_for_production_rows(store) -> None:
    from types import SimpleNamespace

    from repowise.core.persistence.crud import write_function_facts
    from repowise.core.persistence.crud.analysis.evidence import step_findings
    from repowise.core.persistence.models import FunctionFact

    session, repo = store
    await write_function_facts(
        session,
        repo,
        [
            {"symbol_id": "pkg/utils.py::TypeTracker::wrap", "eager_defaults": (("Tracker", 6),)},
            {"symbol_id": "pkg/utils.py::plain"},
            {"symbol_id": "tests/test_utils.py::helper", "eager_defaults": (("Tracker", 3),)},
        ],
    )
    stored = await session.get(FunctionFact, (repo, "pkg/utils.py::TypeTracker::wrap"))
    assert stored is not None and json.loads(stored.eager_defaults_json) == [["Tracker", 6]]
    plain = await session.get(FunctionFact, (repo, "pkg/utils.py::plain"))
    assert plain is not None and plain.eager_defaults_json is None

    def hot(symbol: str, drivers: list[dict] | None = None) -> SimpleNamespace:
        return SimpleNamespace(symbol_id=symbol, drivers_json=json.dumps(drivers or []))

    # The constructor's own row reads its driver's fact; a test file's is left out.
    rows = [
        hot("pkg/utils.py::Tracker::__init__", [{"symbol": "pkg/utils.py::TypeTracker::wrap"}]),
        hot("tests/test_utils.py::helper"),
    ]
    assert await step_findings(session, repo, rows) == [
        {
            "kind": "eager_default",
            "symbol": "pkg/utils.py::TypeTracker::wrap",
            "file_path": "pkg/utils.py",
            "function": "wrap",
            "builds": [("Tracker", 6)],
        }
    ]
