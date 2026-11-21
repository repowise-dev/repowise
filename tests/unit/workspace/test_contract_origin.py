"""Contract ends in test, docs, tooling, generated and vendored code are left out, and counted."""

from __future__ import annotations

import asyncio

from repowise.core.workspace.contracts import Contract, _production_ends


def _end(path: str, role: str = "consumer", ctype: str = "http") -> Contract:
    return Contract("app", "http::GET::/health", ctype, role, path, "x", 0.9)


def test_test_docs_tooling_and_generated_ends_are_left_out() -> None:
    contracts = [
        _end("frontend/lib/api/client.ts"),
        _end("backend/routers/health.py", "provider"),
        _end("frontend/e2e/login.spec.ts"),
        _end("docs/examples/render_review.mjs"),
        _end("claude-mod/scripts/e2e.mjs"),
        _end("src/gen/client_pb2.py"),
    ]
    kept, dropped = asyncio.run(_production_ends(contracts, None))
    assert [c.file_path for c in kept] == [
        "frontend/lib/api/client.ts",
        "backend/routers/health.py",
    ]
    assert dropped == 4


def test_schema_documents_and_migrations_are_kept_wherever_they_sit() -> None:
    contracts = [
        _end("docs/api/openapi.yaml", "provider"),
        _end("backend/migrations/0001_orders.py", "provider", "data"),
        _end("backend/migrations/0002_users.sql", "provider", "data"),
    ]
    kept, dropped = asyncio.run(_production_ends(contracts, None))
    assert kept == contracts
    assert dropped == 0


def test_a_vendored_copy_is_left_out() -> None:
    kept, dropped = asyncio.run(_production_ends([_end("vendor/swagger-ui/index.js")], None))
    assert (kept, dropped) == ([], 1)


def test_an_index_that_cannot_answer_falls_back_to_the_path() -> None:
    class _Index:
        repo_id = "r"
        session = object()  # no `execute`: an index built for another purpose

    contracts = [_end("frontend/e2e/login.spec.ts"), _end("frontend/lib/api/client.ts")]
    kept, dropped = asyncio.run(_production_ends(contracts, _Index()))
    assert [c.file_path for c in kept] == ["frontend/lib/api/client.ts"]
    assert dropped == 1


def test_an_end_left_out_is_not_counted_as_a_missed_call(tmp_path) -> None:
    from repowise.core.workspace.code_api import CodeSurface
    from repowise.core.workspace.config import ContractConfig
    from repowise.core.workspace.contracts import (
        NON_PRODUCTION_ENDS_STAT,
        _extract_one_repo,
        _RepoExtraction,
    )
    from repowise.core.workspace.extractors.base import make_exclude_predicate
    from repowise.core.workspace.extractors.masstransit import MessageTypeIndex
    from repowise.core.workspace.network_probe import UNRECOGNISED_CALL_FILES_STAT

    example = tmp_path / "docs" / "examples" / "client.js"
    example.parent.mkdir(parents=True)
    example.write_text("fetch('/api/orders', { method: 'POST' });\n", encoding="utf-8")
    cfg = ContractConfig(detect_code_api=False)
    extraction = _RepoExtraction(
        contract_config=cfg,
        exclude=make_exclude_predicate(tuple(cfg.exclude_globs)),
        code_surface=CodeSurface(),
        message_types=MessageTypeIndex(),
        boundaries_by_repo={"app": []},
        workspace_index=None,
    )
    contracts, stats = asyncio.run(_extract_one_repo(extraction, "app", tmp_path))
    assert contracts == []
    assert stats[NON_PRODUCTION_ENDS_STAT] == 1
    assert stats[UNRECOGNISED_CALL_FILES_STAT] == 0


def test_a_data_folders_code_is_left_out_on_its_stored_origin(monkeypatch) -> None:
    """Only a stored origin carries ``data``: the path alone reads production."""
    import repowise.core.evidence.ingest as ingest

    async def _stored(_session, _repo_id, paths):
        return {p: "data" if "/data/" in p else "production" for p in paths}

    monkeypatch.setattr(ingest, "load_origins", _stored)

    class _Index:
        repo_id = "r"
        session = object()

    contracts = [_end("backend/data/problems/p0/solution.js"), _end("frontend/lib/api/client.ts")]
    kept, dropped = asyncio.run(_production_ends(contracts, _Index()))
    assert [c.file_path for c in kept] == ["frontend/lib/api/client.ts"]
    assert dropped == 1
