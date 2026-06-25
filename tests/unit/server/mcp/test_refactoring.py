"""Unit tests for the generate_refactoring_code MCP tool (opt-in enrichment).

Uses the ``mock`` provider (no API calls) and a real temp checkout so the tool
can read the plan's source spans and honor the config gate.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.persistence import crud
from repowise.core.persistence.models import Repository

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


async def _setup(factory, *, enabled: bool) -> tuple[Path, str]:
    repo_dir = Path(tempfile.mkdtemp()) / "mcp-enrich"
    (repo_dir / "pkg").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".repowise").mkdir(exist_ok=True)
    cfg = "provider: mock\n"
    if enabled:
        cfg += "refactoring:\n  llm:\n    enabled: true\n"
    (repo_dir / ".repowise" / "config.yaml").write_text(cfg, encoding="utf-8")
    (repo_dir / "pkg" / "leaf.py").write_text(
        "class GodClass:\n    def a(self):\n        return 1\n", encoding="utf-8"
    )

    async with factory() as session:
        session.add(
            Repository(
                id="r1",
                name="mcp-enrich",
                url="https://github.com/example/mcp-enrich",
                local_path=str(repo_dir),
                default_branch="main",
                settings_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await session.flush()
        await crud.save_refactoring_suggestions(
            session,
            "r1",
            [
                {
                    "refactoring_type": "extract_class",
                    "file_path": "pkg/leaf.py",
                    "target_symbol": "GodClass",
                    "line_start": 1,
                    "line_end": 3,
                    "plan": {"groups": [{"name": None, "methods": ["a"], "fields": []}]},
                    "evidence": {"lcom4": 2, "method_count": 1, "field_count": 0, "wmc": 1},
                    "impact_delta": 1.0,
                    "effort_bucket": "S",
                    "blast_radius": {"dependents_count": 0},
                    "confidence": "high",
                    "source_biomarker": "low_cohesion",
                },
            ],
        )
        await session.commit()
        sid = (await crud.get_refactoring_suggestions(session, "r1"))[0].id
    return repo_dir, sid


@pytest.fixture
def _mcp_globals(factory):
    import repowise.server.mcp_server as mcp_mod

    saved_factory = mcp_mod._session_factory
    saved_path = mcp_mod._repo_path
    yield mcp_mod
    mcp_mod._session_factory = saved_factory
    mcp_mod._repo_path = saved_path


@pytest.mark.asyncio
async def test_generate_code_happy_path(factory, _mcp_globals) -> None:
    from repowise.server.mcp_server import generate_refactoring_code

    repo_dir, sid = await _setup(factory, enabled=True)
    _mcp_globals._session_factory = factory
    _mcp_globals._repo_path = str(repo_dir)

    result = await generate_refactoring_code(sid)
    assert "error" not in result
    assert result["refactoring_type"] == "extract_class"
    assert result["provider"] == "mock"
    assert result["content"]
    assert "_meta" in result


@pytest.mark.asyncio
async def test_generate_code_disabled(factory, _mcp_globals) -> None:
    from repowise.server.mcp_server import generate_refactoring_code

    repo_dir, sid = await _setup(factory, enabled=False)
    _mcp_globals._session_factory = factory
    _mcp_globals._repo_path = str(repo_dir)

    result = await generate_refactoring_code(sid)
    assert result["error"] == "disabled"


@pytest.mark.asyncio
async def test_generate_code_unknown_id(factory, _mcp_globals) -> None:
    from repowise.server.mcp_server import generate_refactoring_code

    repo_dir, _ = await _setup(factory, enabled=True)
    _mcp_globals._session_factory = factory
    _mcp_globals._repo_path = str(repo_dir)

    result = await generate_refactoring_code("deadbeef")
    assert result["error"] == "not_found"
