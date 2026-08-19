"""Regression guard: the transient BlameIndex must not survive into the
metadata that reaches persistence / JSON artifact writers.

The per-file ``BlameIndex`` is in-memory only (consumed by the health
biomarkers). If it leaks into ``git_metadata_list`` the hosted backend's
``json.dumps`` of ``git_metadata.json`` raises "Object of type BlameIndex is
not JSON serializable", which aborts the whole indexing run and leaves repos
with missing artifacts.
"""

from __future__ import annotations

import json

from repowise.core.ingestion.git_indexer.function_blame import BlameIndex
from repowise.core.pipeline.phases.git import drop_transient_git_signals


def test_drop_removes_blame_index_and_keeps_other_fields() -> None:
    meta = {
        "file_path": "a.py",
        "commit_count_total": 12,
        "blame_index": BlameIndex(lines={1: ("abc", 100)}, authors={"abc": ("A", "a@x")}),
        "primary_owner_name": "A",
    }
    git_metadata_list = [meta]

    drop_transient_git_signals(git_metadata_list)

    assert "blame_index" not in meta
    # Non-transient fields are preserved.
    assert meta["file_path"] == "a.py"
    assert meta["primary_owner_name"] == "A"
    # The cleaned list is now JSON-serializable — the exact contract the hosted
    # artifact writer depends on.
    json.dumps(git_metadata_list)


def test_drop_is_idempotent_and_safe_without_blame_index() -> None:
    meta = {"file_path": "b.py", "commit_count_total": 1}
    git_metadata_list = [meta]

    drop_transient_git_signals(git_metadata_list)
    drop_transient_git_signals(git_metadata_list)  # second call must not raise

    assert meta == {"file_path": "b.py", "commit_count_total": 1}


def test_decision_sources_never_read_blame_index() -> None:
    # The orchestrator runs decision extraction concurrently with health via
    # asyncio.gather, while the transient BlameIndex is still attached to the
    # shared git metadata dicts (health consumes it; the drop happens after
    # the gather). That is only sound while no decision source reads it.
    from pathlib import Path

    import repowise.core.analysis.decisions as decisions_pkg

    pkg_dir = Path(decisions_pkg.__file__).parent
    offenders = [
        py.name for py in pkg_dir.rglob("*.py") if "blame_index" in py.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"decision sources reference blame_index ({offenders}); they run "
        "concurrently with health before drop_transient_git_signals, so this "
        "needs a re-think of the analysis-phase gather in orchestrator.py"
    )


def test_drop_cleans_shared_git_meta_map_view() -> None:
    # git_meta_map is built from the same dict objects as git_metadata_list, so
    # stripping the list must clean the map view too.
    meta = {"file_path": "c.py", "blame_index": BlameIndex()}
    git_metadata_list = [meta]
    git_meta_map = {m["file_path"]: m for m in git_metadata_list}

    drop_transient_git_signals(git_metadata_list)

    assert "blame_index" not in git_meta_map["c.py"]
