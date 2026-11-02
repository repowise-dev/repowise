"""Unit tests for the generate_refactoring_code MCP tool (opt-in enrichment).

Stubs the shared chat resolver with a ``MockProvider`` (no API calls) and uses
a real temp checkout so the tool can read the plan's source spans and honor the
config gate.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.persistence import crud
from repowise.core.persistence.models import Repository

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


async def _setup(factory, *, enabled: bool | None) -> tuple[Path, str]:
    repo_dir = Path(tempfile.mkdtemp()) / "mcp-enrich"
    (repo_dir / "pkg").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".repowise").mkdir(exist_ok=True)
    cfg = "provider: anthropic\n"
    if enabled is not None:
        cfg += f"refactoring:\n  llm:\n    enabled: {'true' if enabled else 'false'}\n"
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
async def test_generate_code_happy_path(factory, _mcp_globals, monkeypatch) -> None:
    from repowise.core.providers.llm.mock import MockProvider
    from repowise.server.mcp_server import generate_refactoring_code

    calls: list[dict] = []

    def fake_resolver(**kwargs):
        calls.append(kwargs)
        return MockProvider()

    monkeypatch.setattr(
        "repowise.server.provider_config.get_chat_provider_instance", fake_resolver
    )
    repo_dir, sid = await _setup(factory, enabled=True)
    _mcp_globals._session_factory = factory
    _mcp_globals._repo_path = str(repo_dir)

    result = await generate_refactoring_code(sid)
    assert "error" not in result
    assert result["refactoring_type"] == "extract_class"
    assert result["provider"] == "mock"
    assert result["content"]
    assert "_meta" in result
    # The provider comes from the resolver chat uses, scoped to this repo.
    assert calls == [{"repo_path": repo_dir, "repo_id": "r1"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, None], ids=["explicit_false", "unset"])
async def test_generate_code_disabled(factory, _mcp_globals, enabled) -> None:
    from repowise.server.mcp_server import generate_refactoring_code

    repo_dir, sid = await _setup(factory, enabled=enabled)
    _mcp_globals._session_factory = factory
    _mcp_globals._repo_path = str(repo_dir)

    result = await generate_refactoring_code(sid)
    assert result["resolved"] is True
    # The content-derived plan identity, versioned by its prefix.
    assert result["suggestion_id"].startswith("refac4_")
    assert result["generation"]["available"] is False
    assert result["generation"]["reason"] == "disabled"


@pytest.mark.asyncio
async def test_generate_code_unknown_id(factory, _mcp_globals) -> None:
    from repowise.server.mcp_server import generate_refactoring_code

    repo_dir, _ = await _setup(factory, enabled=True)
    _mcp_globals._session_factory = factory
    _mcp_globals._repo_path = str(repo_dir)

    result = await generate_refactoring_code("deadbeef")
    assert result["error"] == "not_found"
