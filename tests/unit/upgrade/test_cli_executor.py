"""Tests for the CLI-side upgrade executor (``repowise.cli.upgrade``).

These cover the edge that ``assess`` itself does not: reading the store's
``config.yaml`` for the pinned embedding model, the ``UpgradeContext``
implementation's side effects, and the best-effort resolution of the current
embedding model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli import upgrade as cliup
from repowise.cli.helpers import save_config, save_state
from repowise.core.upgrade import UpgradeActionKind, UpgradeTier


def test_assess_store_clean_on_fresh_store(tmp_path: Path):
    save_state(tmp_path, {"last_sync_commit": "abc"})
    verdict = cliup.assess_store(tmp_path)
    # save_state stamps the current format, so a freshly written store is a noop.
    assert verdict.is_noop
    assert verdict.tier == UpgradeTier.COMPATIBLE


def test_assess_store_detects_embedding_drift(tmp_path: Path, monkeypatch):
    save_state(tmp_path, {"last_sync_commit": "abc"})
    save_config(
        tmp_path,
        "openai",
        "gpt-5.4-nano",
        "openai",
        embedding_model="text-embedding-3-large",
    )
    # Running build now resolves a different model.
    monkeypatch.setattr(cliup, "_current_embedding_model", lambda: "text-embedding-3-small")
    verdict = cliup.assess_store(tmp_path)
    assert verdict.tier == UpgradeTier.AUTO
    assert any(a.kind == UpgradeActionKind.REEMBED_VECTORS for a in verdict.actions)


def test_drop_parse_cache_unlinks_file(tmp_path: Path):
    cache = tmp_path / ".repowise" / "parse_cache.pkl"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"stale")
    ctx = cliup._CliUpgradeContext(tmp_path)
    ctx.drop_parse_cache()
    assert not cache.exists()


def test_drop_parse_cache_is_noop_when_absent(tmp_path: Path):
    ctx = cliup._CliUpgradeContext(tmp_path)
    ctx.drop_parse_cache()  # must not raise when the cache file is missing


def test_current_embedding_model_swallows_errors(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("no providers")

    monkeypatch.setattr("repowise.cli.providers.embedders.resolve_embedder", _boom, raising=False)
    assert cliup._current_embedding_model() is None


@pytest.mark.asyncio
async def test_apply_upgrade_noop_when_no_actions(tmp_path: Path):
    verdict = cliup.assess_store(tmp_path)  # empty store -> noop verdict
    await cliup.apply_upgrade(tmp_path, verdict)  # must not raise / do nothing
