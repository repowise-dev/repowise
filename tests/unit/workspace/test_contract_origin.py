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
