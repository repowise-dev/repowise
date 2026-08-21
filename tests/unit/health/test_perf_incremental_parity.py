from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from repowise.core.analysis.execution_graph import ExecutionGraphIndex
from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.persistence.database import create_engine, create_session_factory, init_db
from repowise.core.persistence.models import HealthFinding
from repowise.core.pipeline.incremental import load_stored_performance_callers
from tests.unit.persistence.helpers import insert_repo


def _repo(tmp_path: Path):
    files = {
        "db.py": (
            "from sqlalchemy import select\n\n"
            "def fetch_one(session, rid):\n"
            "    return session.execute(select(rid))\n"
        ),
        "service.py": (
            "from db import fetch_one\n\n"
            "def fetch_all(session, ids):\n"
            "    for rid in ids:\n"
            "        fetch_one(session, rid)\n"
        ),
        "service_b.py": (
            "from db import fetch_one\n\n"
            "def fetch_more(session, ids):\n"
            "    for rid in ids:\n"
            "        fetch_one(session, rid)\n"
        ),
        "unrelated.py": "def pure(value):\n    return value + 1\n",
    }
    for name, source in files.items():
        (tmp_path / name).write_text(source, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo_path=tmp_path)
    parsed = []
    for info in FileTraverser(tmp_path).traverse():
        item = parser.parse_file(info, Path(info.abs_path).read_bytes())
        builder.add_file(item)
        parsed.append(item)
    return parsed, builder.build()


def _performance_signature(report):
    return sorted(
        (
            finding.file_path,
            finding.biomarker_type,
            finding.line_start,
            finding.details.get("opportunity_id"),
            tuple(finding.details.get("path", ())),
        )
        for finding in report.findings
        if finding.dimension == "performance"
    )


@pytest.mark.parametrize("changed_file", ["service.py", "db.py"])
def test_changed_caller_and_changed_sink_match_full_performance_analysis(
    tmp_path: Path, changed_file: str
):
    parsed, graph = _repo(tmp_path)
    full = HealthAnalyzer(graph, parsed_files=parsed, repo_root=tmp_path).analyze()
    scope = ExecutionGraphIndex(graph).affected_files({changed_file})
    partial = HealthAnalyzer(graph, parsed_files=parsed, repo_root=tmp_path).analyze(
        changed_files=scope
    )

    assert scope == {"service.py", "service_b.py", "db.py"}
    assert _performance_signature(partial) == _performance_signature(full)
    full_plans = [
        suggestion.plan
        for suggestion in full.refactoring_suggestions
        if suggestion.refactoring_type == "performance_fix"
    ]
    partial_plans = [
        suggestion.plan
        for suggestion in partial.refactoring_suggestions
        if suggestion.refactoring_type == "performance_fix"
    ]
    assert partial_plans == full_plans


@pytest.mark.asyncio
async def test_old_side_performance_paths_recover_callers_of_deleted_sink(tmp_path: Path):
    (tmp_path / ".repowise").mkdir()
    db_path = (tmp_path / ".repowise" / "wiki.db").as_posix()
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        repo = await insert_repo(session, local_path=str(tmp_path))
        session.add(
            HealthFinding(
                id=uuid.uuid4().hex,
                repository_id=repo.id,
                file_path="caller.py",
                biomarker_type="io_in_loop",
                severity="medium",
                function_name="run",
                line_start=4,
                line_end=4,
                details_json=json.dumps(
                    {
                        "cross_function": True,
                        "boundary_kind": "db",
                        "path": ["caller.py::run", "deleted.py::fetch"],
                    }
                ),
                health_impact=0.0,
                reason="old N+1",
                dimension="performance",
                status="open",
            )
        )
        await session.commit()

    assert await load_stored_performance_callers(tmp_path, {"deleted.py"}) == {"caller.py"}
    await engine.dispose()
