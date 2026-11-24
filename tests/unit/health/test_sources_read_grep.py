"""Finalize asks git which test files never name ``getsource`` before reading any.

Opening every test file the Extract Method plans run cost seconds a run on a
cold disk. Skipping the ones git lists must leave every stored row as it was.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from repowise.core.analysis.health.refactoring import annotations

_FLOW = "import inspect\nimport svc.orders as so\nsrc = inspect.getsource(so.handle)\n"
_PLAIN = "from svc.orders import handle\n\ndef test_handle():\n    assert handle(1)\n"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")


def _read_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """The path before git was asked: every file is opened."""
    monkeypatch.setattr(annotations, "_lacking_getsource", lambda root: frozenset())


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _write(
        root,
        {
            "svc/orders.py": "def handle(x):\n    return x\n",
            "tests/test_flow.py": _FLOW,
            "tests/test_plain.py": _PLAIN,
            "tests/test_edited.py": _PLAIN,
        },
    )
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    # The working tree decides, not the commit: an edit adds a read after it.
    (root / "tests" / "test_edited.py").write_text(
        _PLAIN + "import inspect\nlines = inspect.getsourcelines(so.handle)\n", encoding="utf-8"
    )
    # Git never lists an untracked file, so it is read.
    _write(root, {"tests/test_new.py": _FLOW})
    return root


def test_git_skips_only_files_that_never_name_getsource(checkout, monkeypatch) -> None:
    files = [
        "tests/test_flow.py",
        "tests/test_plain.py",
        "tests/test_edited.py",
        "tests/test_new.py",
        "tests/missing.py",
        "../outside.py",
    ]
    (checkout.parent / "outside.py").write_text(_FLOW, encoding="utf-8")
    with contextlib.suppress(OSError):  # Windows without the privilege makes no link
        (checkout / "tests" / "test_link.py").symlink_to(checkout / "tests" / "test_flow.py")
        _git(checkout, "add", "tests/test_link.py")
        files.append("tests/test_link.py")

    opened: list[str] = []
    read_text = Path.read_text

    def counted(self: Path, *args: Any, **kwargs: Any) -> str:
        opened.append(self.name)
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    fast = annotations._sources_read(checkout, files)
    assert "test_plain.py" not in opened
    assert "test_flow.py" in opened and "test_new.py" in opened
    with monkeypatch.context() as m:
        _read_all(m)
        assert annotations._sources_read(checkout, files) == fast
    assert fast["tests/test_edited.py"] == frozenset({"so.handle"})
    assert fast["tests/test_new.py"] == frozenset({"orders.handle"})
    # A root below the repository top is answered relative to itself.
    sub = annotations._sources_read(checkout / "tests", ["test_flow.py", "test_plain.py"])
    assert sub == {"test_flow.py": frozenset({"orders.handle"})}


def test_a_root_git_cannot_answer_reads_every_file(tmp_path: Path) -> None:
    _write(tmp_path, {"tests/test_flow.py": _FLOW, "tests/test_plain.py": _PLAIN})
    assert annotations._lacking_getsource(tmp_path / "missing") == frozenset()
    read = annotations._sources_read(tmp_path, ["tests/test_flow.py", "tests/test_plain.py"])
    assert read == {"tests/test_flow.py": frozenset({"orders.handle"})}


async def _finalize(db: Path, checkout: Path) -> dict[str, list[tuple]]:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        crud,
        get_session,
    )

    engine = create_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo_id = (await session.execute(text("select id from repositories"))).scalar()
            await crud.finalize_refactoring_opportunities(
                session, repo_id, analyzed_commit="c" * 40
            )
            await session.commit()
            # Every table finalize can write, not a chosen few: the plan rows
            # with their stored rank and annotations, opportunities, summaries.
            tables = (
                await session.execute(
                    text("select name from sqlite_master where type = 'table' order by name")
                )
            ).scalars()
            out: dict[str, list[tuple]] = {}
            for table in tables:
                result = await session.execute(text(f"select * from {table}"))
                # A fresh surrogate id and the write time differ on any two runs.
                keep = [
                    i for i, c in enumerate(result.keys()) if c != "id" and not c.endswith("_at")
                ]
                out[table] = sorted(repr(tuple(row[i] for i in keep)) for row in result.all())
            return out
    finally:
        await engine.dispose()


async def _seed(db: Path, checkout: Path) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        crud,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.models import GraphNode

    engine = create_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(checkout))
            for path, is_test in (
                ("svc/orders.py", False),
                ("tests/test_flow.py", True),
                ("tests/test_plain.py", True),
                ("tests/test_orders.py", True),
                ("tests/svc/test_orders.py", True),
            ):
                session.add(
                    GraphNode(
                        repository_id=repo.id,
                        node_id=path,
                        node_type="file",
                        file_path=path,
                        is_test=is_test,
                    )
                )
            await crud.save_health_findings(
                session,
                repo.id,
                [
                    {
                        "file_path": "svc/orders.py",
                        "biomarker_type": "complex_method",
                        "severity": "high",
                        "function_name": "handle",
                        "line_start": 1,
                        "line_end": 30,
                        "details": {"ccn": 85},
                        "health_impact": 3.0,
                        "reason": "seeded",
                        "dimension": "defect",
                    }
                ],
            )
            await crud.save_refactoring_suggestions(
                session,
                repo.id,
                [
                    {
                        "refactoring_type": "extract_method",
                        "file_path": "svc/orders.py",
                        "target_symbol": "handle",
                        "line_start": 1,
                        "line_end": 30,
                        "plan": {"span": {"start": 2, "end": 28}, "params": ["x"], "returns": []},
                        "evidence": {"ccn_removed": 6, "slice_nloc": 20},
                        "impact_delta": 2.0,
                        "effort_bucket": "S",
                        "blast_radius": {"scope": "local"},
                        "confidence": "high",
                        "source_biomarker": "complex_method",
                    }
                ],
            )
            await session.commit()
    finally:
        await engine.dispose()


async def test_finalize_stores_the_same_rows_with_and_without_the_grep(
    checkout, tmp_path, monkeypatch
) -> None:
    # Two tests named for the plan's file: one reads its source, one does not.
    _write(checkout, {"tests/test_orders.py": _FLOW, "tests/svc/test_orders.py": _PLAIN})
    _git(checkout, "add", "tests")
    base = tmp_path / "base.db"
    await _seed(base, checkout)
    fast_db, full_db = tmp_path / "fast.db", tmp_path / "full.db"
    shutil.copy(base, fast_db)
    shutil.copy(base, full_db)

    fast = await _finalize(fast_db, checkout)
    with monkeypatch.context() as m:
        _read_all(m)
        full = await _finalize(full_db, checkout)
    assert fast == full
    # The plan runs the tests that read its target's source, so the risk is
    # in the stored rows both ways rather than absent from both.
    stored = repr(fast["refactoring_suggestions"])
    assert "tests/svc/test_orders.py" in stored and "source_text" in stored
