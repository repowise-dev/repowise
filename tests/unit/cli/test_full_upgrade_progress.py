"""``repowise update --full`` must show progress and persist incrementally (#1709).

The full-upgrade path used to pass ``progress=None`` into generation (no-ops
every progress callback — a long run showed a dead screen) and buffered every
page in memory until the end (a Ctrl-C at hour eight cost the whole run). It
now routes through init's ``run_generation_with_persistence`` wrapper, which
gives progress, incremental per-page saves and resume.

The wrapper call lives inside an async closure that needs real DB/graph
objects, so like the strict-branch guards in test_coverage_cmd.py this is a
structural pin: it asserts the call site names the persistence wrapper with a
real progress callback, and can only fail if someone restores the old
buffered ``progress=None`` path.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from repowise.cli.commands import upgrade_flow
from repowise.core.pipeline.cleanup_debt import load_cleanup_debt


def _upgrade_source() -> str:
    return inspect.getsource(upgrade_flow._run_upgrade)


def test_full_upgrade_uses_the_persistence_wrapper() -> None:
    src = _upgrade_source()

    assert "run_generation_with_persistence" in src, (
        "the full upgrade must route generation through init's persistence "
        "wrapper so pages are saved as they complete"
    )


def test_full_upgrade_passes_a_real_progress_callback() -> None:
    src = _upgrade_source()

    assert "RichProgressCallback" in src, (
        "the full upgrade must attach a live progress bar, not progress=None"
    )
    # The callback is wired to the same rich Progress init uses.
    assert "Progress(" in src


def test_full_upgrade_estimate_names_structural_pages() -> None:
    src = inspect.getsource(upgrade_flow._gate_cost)

    # The estimate line must call the summary that says most pages never reach
    # a model — otherwise "3661 pages" reads as 3661 model calls.
    assert "structural_page_summary" in src


def test_full_upgrade_reconciles_and_cleans_retired_pages() -> None:
    src = _upgrade_source()
    persistence_src = inspect.getsource(upgrade_flow._persist_authoritative_pages)

    assert "_persist_authoritative_pages" in src
    assert "tombstone_pages_outside_generation" in persistence_src
    assert "vector_store.delete_many(retired_page_ids)" in persistence_src
    assert "_sync_authoritative_fts" in src


def test_full_upgrade_fts_cleanup_retries_after_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0
    deleted: list[list[str]] = []
    indexed: list[str] = []

    class _FakeFts:
        def __init__(self, _engine) -> None:
            pass

        async def ensure_index(self) -> None:
            pass

        async def delete_many(self, page_ids: list[str]) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary FTS failure")
            deleted.append(page_ids)

        async def index(self, page_id: str, *_args, **_kwargs) -> None:
            indexed.append(page_id)

    import repowise.core.persistence.search as search

    monkeypatch.setattr(search, "FullTextSearch", _FakeFts)
    page = SimpleNamespace(
        page_id="file_page:current.py",
        title="Current",
        content="# Current",
        summary="Current page",
        target_path="current.py",
    )

    with pytest.raises(RuntimeError, match="temporary FTS failure"):
        asyncio.run(
            upgrade_flow._sync_authoritative_fts(
                engine=object(),
                repo_path=tmp_path,
                generated_pages=[page],
                retired_page_ids=["file_page:obsolete.py"],
            )
        )

    assert load_cleanup_debt(tmp_path)["fts"] == {"file_page:obsolete.py"}

    # SQL already committed the tombstone, so the retry discovers no newly
    # retired ids. Durable cleanup debt must replay the missing deletion.
    asyncio.run(
        upgrade_flow._sync_authoritative_fts(
            engine=object(),
            repo_path=tmp_path,
            generated_pages=[page],
            retired_page_ids=[],
        )
    )

    assert deleted == [["file_page:obsolete.py"]]
    assert indexed == ["file_page:current.py"]
    assert load_cleanup_debt(tmp_path)["fts"] == set()
