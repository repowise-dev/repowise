"""A configuration-driven template rebuild embeds each final page once."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.helpers import (
    config_fingerprint,
    load_state,
    release_update_lock,
    save_config_partial,
)
from repowise.cli.main import cli
from repowise.core.persistence.vector_store import InMemoryVectorStore, embed_item
from repowise.core.providers.embedding.base import MockEmbedder


class RecordingStore(InMemoryVectorStore):
    def __init__(self):
        super().__init__(MockEmbedder())
        self.items: list[tuple[str, str, dict]] = []
        self.fail = False

    async def embed_batch(self, items):
        if self.fail:
            raise RuntimeError("fixture embedding failure")
        self.items.extend(deepcopy(items))
        await super().embed_batch(items)


@pytest.fixture
def indexed_template_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (repo / "beta.py").write_text(
        "from alpha import alpha\n\ndef beta():\n    return alpha() + 1\n", encoding="utf-8"
    )
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "seed"],
    ):
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    store = RecordingStore()
    monkeypatch.setattr("repowise.cli.providers.build_embedder", lambda *_a, **_k: MockEmbedder())
    monkeypatch.setattr("repowise.cli.providers.build_vector_store", lambda *_a, **_k: store)
    result = CliRunner().invoke(
        cli,
        [
            "init",
            str(repo),
            "--yes",
            "--no-prose",
            "--embedder",
            "ollama",
            "--no-editor-setup",
            "--no-hook",
            "--no-agents",
            "--no-workspace",
            "--no-onboarding",
            "--commit-limit",
            "1",
            "--max-file-pages",
            "0",
            "--no-cost-tracking",
        ],
    )
    assert result.exit_code == 0, result.output
    store.items.clear()
    save_config_partial(repo, wiki_style="reference")
    try:
        yield repo, store
    finally:
        # CliRunner stays in this process; the CLI normally releases at exit.
        release_update_lock(repo)


def _update(repo: Path):
    return CliRunner().invoke(
        cli, ["update", str(repo), "--index-only", "--no-workspace", "--no-agents"]
    )


def test_config_rebuild_embeds_each_final_page_once(indexed_template_repo):
    repo, store = indexed_template_repo
    result = _update(repo)
    assert result.exit_code == 0, result.output
    assert store.items
    counts = Counter(page_id for page_id, _, _ in store.items)
    assert set(counts.values()) == {1}, counts

    # Compare the actual embedding input with the final persisted page recipe,
    # including post-generation wiki links, summary, digest and vocabulary.
    with sqlite3.connect(repo / ".repowise" / "wiki.db") as db:
        rows = db.execute(
            "SELECT id, title, page_type, target_path, summary, content, metadata_json, digest "
            "FROM wiki_pages WHERE freshness_status != 'tombstone'"
        ).fetchall()
    expected = [
        item
        for page_id, title, page_type, target_path, summary, content, metadata, digest in rows
        if (
            item := embed_item(
                page_id,
                title=title,
                page_type=page_type,
                target_path=target_path,
                summary=summary or "",
                content=content,
                page_metadata=metadata,
                digest=digest or "",
            )
        )
        is not None
    ]
    assert sorted(store.items) == sorted(expected)
    assert load_state(repo)["config_fingerprint"] == config_fingerprint(repo)
    assert asyncio.run(store.list_page_ids()) == {page_id for page_id, _, _ in expected}


def test_config_embedding_failure_retains_previous_fingerprint(indexed_template_repo):
    repo, store = indexed_template_repo
    before = load_state(repo)["config_fingerprint"]
    store.fail = True
    result = _update(repo)
    assert result.exit_code != 0
    assert load_state(repo)["config_fingerprint"] == before
    assert before != config_fingerprint(repo)
