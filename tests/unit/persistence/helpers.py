"""Shared helper functions for persistence unit tests.

These are module-level functions (not fixtures) so they can be imported
from any test file without conftest import restrictions.
"""

from __future__ import annotations

from repowise.core.persistence.models import Repository


def make_repo_kwargs(**overrides) -> dict:
    return {
        "name": "test-repo",
        "local_path": "/tmp/test-repo",
        "url": "https://github.com/example/test-repo",
        **overrides,
    }


def make_page_kwargs(repo_id: str, **overrides) -> dict:
    return {
        "page_id": "file_page:src/main.py",
        "repository_id": repo_id,
        "page_type": "file_page",
        "title": "main.py",
        "content": "# Main module\n\nEntry point for the application.",
        "target_path": "src/main.py",
        "source_hash": "abc123",
        "model_name": "mock",
        "provider_name": "mock",
        "input_tokens": 100,
        "output_tokens": 50,
        **overrides,
    }


async def insert_repo(session, **overrides) -> Repository:
    from repowise.core.persistence.crud import upsert_repository

    repo = await upsert_repository(session, **make_repo_kwargs(**overrides))
    await session.commit()
    return repo
