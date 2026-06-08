"""record_mcp_saving — writes a mcp:<tool> ledger row, degrades silently."""

from __future__ import annotations

from pathlib import Path

from repowise.core.distill.store import OmissionStore
from repowise.server.mcp_server._savings.recorder import record_mcp_saving


def _repo_db(repo: Path) -> Path:
    return repo / ".repowise" / "omissions" / "omissions.db"


def _init_repo(tmp_path: Path) -> Path:
    """Create a repo with an opt-in repo-local omission sidecar; return its root."""
    OmissionStore(_repo_db(tmp_path)).close()  # materialise the file
    return tmp_path


def test_records_mcp_row(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert record_mcp_saving(repo, "get_symbol", replaced_tokens=3000, delivered_tokens=300)

    store = OmissionStore(_repo_db(repo))
    try:
        rollup = {r["group"]: r for r in store.savings_rollup(by="source")}
    finally:
        store.close()
    assert rollup["mcp:get_symbol"]["saved_tokens"] == 2700
    assert rollup["mcp:get_symbol"]["events"] == 1


def test_skips_when_no_net_saving(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    # replaced <= delivered → no row.
    assert not record_mcp_saving(repo, "get_context", replaced_tokens=200, delivered_tokens=200)
    assert not record_mcp_saving(repo, "get_context", replaced_tokens=100, delivered_tokens=500)

    store = OmissionStore(_repo_db(repo))
    try:
        assert store.savings_summary()["events"] == 0
    finally:
        store.close()


def test_no_store_degrades_silently(tmp_path: Path) -> None:
    # A repo that never ran `repowise init` has no sidecar; we must not create
    # one from the read-side, and must not raise.
    assert not record_mcp_saving(tmp_path, "get_symbol", replaced_tokens=3000, delivered_tokens=10)
    assert not (tmp_path / ".repowise").exists()
