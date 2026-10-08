"""Served targets with uncommitted edits are marked, never called fresh.

A real keyless index of a tmp git repo, then uncommitted edits: a rename of a
function and its caller, an added function, a deleted function and a body
edit. Every tool response about an affected target either reflects the live
tree or carries the stale marker; a response that does neither is a silent
wrong answer, and there must be none. ``repowise update --working-tree``
then indexes the edits, and the marker clears.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.server.mcp_server import _meta, _server, _state
from tests.unit.cli.test_update_e2e import _git, _index_full

_FILES = {
    "billing.py": "def compute_total(items):\n    return sum(items)\n",
    "checkout.py": (
        "from billing import compute_total\n\n\n"
        "def place_order(items):\n    return compute_total(items) + 1\n"
    ),
    "growth.py": "def existing_metric():\n    return 7\n",
    "legacy.py": (
        "def kept_helper():\n    return 1\n\n\n"
        "def retired_exporter():\n    return 'csv'\n"
    ),
    "pricing.py": "def base_price():\n    return 41\n",
}

_EDITS = {
    "billing.py": "def sum_line_amounts(items):\n    return sum(items)\n",
    "checkout.py": (
        "from billing import sum_line_amounts\n\n\n"
        "def place_order(items):\n    return sum_line_amounts(items) + 1\n"
    ),
    "growth.py": (
        "def existing_metric():\n    return 7\n\n\n"
        "def weekly_signup_rate():\n    return 0.5\n"
    ),
    "legacy.py": "def kept_helper():\n    return 1\n",
    "pricing.py": "def base_price():\n    return 99173\n",
}

# (case, file, symbol an agent would ask for, tokens only the old code has,
# tokens only the live code has)
_CASES = [
    ("rename", "billing.py", "sum_line_amounts", ["compute_total"], ["sum_line_amounts"]),
    ("rename_caller", "checkout.py", "place_order", ["compute_total"], ["sum_line_amounts"]),
    ("add", "growth.py", "weekly_signup_rate", [], ["weekly_signup_rate"]),
    ("delete", "legacy.py", "retired_exporter", ["retired_exporter"], []),
    ("body", "pricing.py", "base_price", ["return 41"], ["99173"]),
]


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    for name, body in _FILES.items():
        (repo / name).write_text(body)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _body(response: dict) -> str:
    # The caller's own words are echoed back; they say nothing about the index.
    return json.dumps(
        {k: v for k, v in response.items() if k not in {"_meta", "query", "question"}},
        default=str,
    )


def _marked(response: dict) -> bool:
    return bool((response.get("_meta") or {}).get("stale_warning"))


async def _responses(case_file: str, symbol: str) -> dict[str, dict]:
    from repowise.server.mcp_server.tool_answer.answer import get_answer
    from repowise.server.mcp_server.tool_context.context import get_context
    from repowise.server.mcp_server.tool_risk.get_risk import get_risk
    from repowise.server.mcp_server.tool_search import search_codebase
    from repowise.server.mcp_server.tool_symbol import get_symbol

    return {
        "get_context": await get_context(targets=[case_file]),
        "get_symbol": await get_symbol(f"{case_file}::{symbol}"),
        "search_codebase": await search_codebase(symbol),
        "get_answer": await get_answer(f"what does {symbol} in {case_file} do"),
        "get_risk": await get_risk(targets=[case_file]),
    }


async def _serve(repo: Path) -> dict[tuple[str, str], dict]:
    _meta._dirty_paths_cache.clear()
    _state._repo_path = str(repo)
    _state._force_single_repo = True
    out: dict[tuple[str, str], dict] = {}
    try:
        async with _server._lifespan(_server.mcp):
            for case, case_file, symbol, _old, _new in _CASES:
                for tool, response in (await _responses(case_file, symbol)).items():
                    out[(case, tool)] = response
    finally:
        _state._repo_path = None
        _state._force_single_repo = False
    return out


_TOOLS = ("get_context", "get_symbol", "search_codebase", "get_answer", "get_risk")


def _silent_wrong(
    responses: dict[tuple[str, str], dict], *, reverted: bool = False
) -> list[tuple[str, str]]:
    """Unmarked responses that serve the target or an old fact but not the live code.

    A reply that names neither the file nor anything the edit removed (a
    retrieval miss) makes no claim about the target, so it is not counted.
    ``reverted`` swaps the sides: the edits are what is gone.
    """
    wrong = []
    for case, case_file, _symbol, old, new in _CASES:
        if reverted:
            old, new = new, old
        for tool in _TOOLS:
            response = responses[(case, tool)]
            body = _body(response)
            stale_fact = any(t in body for t in old)
            live = all(t in body for t in new) and not stale_fact
            about_target = case_file in body or stale_fact
            if about_target and not live and not _marked(response):
                wrong.append((case, tool))
    return wrong


def test_uncommitted_edits_are_marked_and_cleared_by_working_tree_update(
    tmp_path: Path, monkeypatch
) -> None:
    from repowise.cli.helpers import release_update_lock, save_state

    monkeypatch.setenv("REPOWISE_EMBEDDER", "mock")
    for name in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    repo = _make_repo(tmp_path)
    _index_full(repo)
    save_state(repo, {"last_sync_commit": _git(repo, "rev-parse", "HEAD"), "docs_enabled": False})
    for name, body in _EDITS.items():
        (repo / name).write_text(body)

    before = asyncio.run(_serve(repo))
    assert _silent_wrong(before) == []
    context = before[("rename", "get_context")]
    assert context["_meta"]["working_tree_dirty"] == 1
    assert "repowise update --working-tree" in context["_meta"]["stale_warning"]
    card = context["targets"]["billing.py"]
    assert card["freshness"]["working_tree"] == "modified"

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace", "--working-tree"])
    assert result.exit_code == 0, result.output

    after = asyncio.run(_serve(repo))
    _assert_clear(after)
    card = after[("rename", "get_context")]["targets"]["billing.py"]
    assert "working_tree" not in card["freshness"]

    # Revert without committing: git status is clean, but the index still
    # holds the edits, so every served target is marked until the next update.
    _git(repo, "checkout", "--", ".")
    reverted = asyncio.run(_serve(repo))
    assert _silent_wrong(reverted, reverted=True) == []
    meta = reverted[("rename", "get_context")]["_meta"]
    assert "no longer in the working tree" in meta["stale_warning"]
    assert "working_tree_dirty" not in meta

    # The update lock is released at process exit; this run shares the process.
    release_update_lock(repo)
    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace", "--working-tree"])
    assert result.exit_code == 0, result.output
    recorded = json.loads((repo / ".repowise" / "state.json").read_text())["working_tree_paths"]
    assert not set(recorded) & set(_EDITS)
    _assert_clear(asyncio.run(_serve(repo)), reverted=True)


def _assert_clear(responses: dict[tuple[str, str], dict], *, reverted: bool = False) -> None:
    """Covered: no marker, and nothing that is gone is served as current."""
    for case, _file, _symbol, old, new in _CASES:
        gone = new if reverted else old
        for tool in _TOOLS:
            response = responses[(case, tool)]
            assert "working_tree_dirty" not in response["_meta"], (case, tool)
            assert not _marked(response), (case, tool, response["_meta"])
            # A not-found reply echoes the name it was asked for; that is the
            # live answer for a deleted symbol, not a served fact.
            if response.get("error") or response.get("results") == []:
                continue
            body = _body(response)
            assert not any(t in body for t in gone), (case, tool, body[:1500])


# The cases below need git only, no index: the freshness logic on its own.


def _state_repo(tmp_path: Path):
    import types

    repo = _make_repo(tmp_path)
    (repo / ".repowise").mkdir()
    head = _git(repo, "rev-parse", "HEAD")
    (repo / ".repowise" / "state.json").write_text(json.dumps({"last_sync_commit": head}))
    row = types.SimpleNamespace(updated_at=None, local_path=str(repo), head_commit=head)
    _meta._dirty_paths_cache.clear()
    return repo, row


def _record(repo: Path, paths: list[str]) -> None:
    state_path = repo / ".repowise" / "state.json"
    state = json.loads(state_path.read_text())
    state["working_tree_paths"] = paths
    state_path.write_text(json.dumps(state))
    _meta._dirty_paths_cache.clear()


def test_a_clean_tree_adds_nothing(tmp_path: Path) -> None:
    _repo, row = _state_repo(tmp_path)
    out = _meta.freshness_from_repo(row, targets=["billing.py::compute_total"])
    assert "working_tree_dirty" not in out
    assert "stale_warning" not in out
    assert out["index_behind"] is False


def test_only_served_dirty_targets_warn(tmp_path: Path) -> None:
    repo, row = _state_repo(tmp_path)
    (repo / "pricing.py").write_text(_EDITS["pricing.py"])

    unrelated = _meta.freshness_from_repo(row, targets=["billing.py"])
    assert "working_tree_dirty" not in unrelated
    assert "stale_warning" not in unrelated
    # A repo-level response never warns just because the tree is dirty.
    assert "stale_warning" not in _meta.freshness_from_repo(row, targets=None)

    served = _meta.freshness_from_repo(row, targets=["pricing.py::base_price", "billing.py"])
    assert served["working_tree_dirty"] == 1
    assert "repowise update --working-tree" in served["stale_warning"]


def test_untracked_files_and_directories_count(tmp_path: Path) -> None:
    repo, _row = _state_repo(tmp_path)
    (repo / "fresh").mkdir()
    (repo / "fresh" / "new.py").write_text("def brand_new():\n    return 1\n")
    (repo / "loose.py").write_text("x = 1\n")
    assert _meta.uncommitted_targets(str(repo), ["fresh/new.py", "loose.py", "fresh"]) == [
        "fresh/new.py",
        "loose.py",
        "fresh",
    ]


def test_a_recorded_working_tree_update_covers_until_the_next_edit(tmp_path: Path) -> None:
    import os

    repo, row = _state_repo(tmp_path)
    (repo / "pricing.py").write_text(_EDITS["pricing.py"])
    (repo / "legacy.py").unlink()
    stamp = (repo / "pricing.py").stat().st_mtime
    os.utime(repo / "pricing.py", (stamp - 10, stamp - 10))
    os.utime(repo, (stamp - 10, stamp - 10))
    _record(repo, ["legacy.py", "pricing.py"])

    out = _meta.freshness_from_repo(row, targets=["pricing.py", "legacy.py"])
    assert "working_tree_dirty" not in out
    assert "stale_warning" not in out

    later = (repo / ".repowise" / "state.json").stat().st_mtime + 10
    os.utime(repo / "pricing.py", (later, later))
    _meta._dirty_paths_cache.clear()
    assert _meta.freshness_from_repo(row, targets=["pricing.py"])["working_tree_dirty"] == 1


def test_git_failure_is_not_evaluated(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    repo, row = _state_repo(tmp_path)
    (repo / "pricing.py").write_text(_EDITS["pricing.py"])

    def _boom(*_a, **_k):
        raise subprocess.TimeoutExpired("git", 2)

    monkeypatch.setattr(subprocess, "run", _boom)
    out = _meta.freshness_from_repo(row, targets=["pricing.py"])
    assert "working_tree_dirty" not in out
    assert "stale_warning" not in out


def test_an_untracked_directory_is_covered_only_file_by_file(tmp_path: Path) -> None:
    import os

    repo, _row = _state_repo(tmp_path)
    (repo / "fresh").mkdir()
    new = repo / "fresh" / "new.py"
    new.write_text("def brand_new():\n    return 1\n")
    old = new.stat().st_mtime - 10
    os.utime(new, (old, old))
    os.utime(repo / "fresh", (old, old))

    # The directory alone in the record covers nothing inside it.
    _record(repo, ["fresh"])
    assert _meta.uncommitted_targets(str(repo), ["fresh/new.py"]) == ["fresh/new.py"]

    _record(repo, ["fresh/new.py"])
    assert _meta.uncommitted_targets(str(repo), ["fresh/new.py", "fresh"]) == []

    # Editing a file inside leaves the directory's own mtime alone.
    later = (repo / ".repowise" / "state.json").stat().st_mtime + 10
    os.utime(new, (later, later))
    os.utime(repo / "fresh", (old, old))
    _meta._dirty_paths_cache.clear()
    assert _meta.uncommitted_targets(str(repo), ["fresh/new.py"]) == ["fresh/new.py"]


def test_target_case_is_folded_where_the_filesystem_folds_it(tmp_path: Path, monkeypatch) -> None:
    repo, _row = _state_repo(tmp_path)
    (repo / "pricing.py").write_text(_EDITS["pricing.py"])
    monkeypatch.setattr(_meta.sys, "platform", "win32")
    assert _meta.uncommitted_targets(str(repo), ["Pricing.py"]) == ["Pricing.py"]
    monkeypatch.setattr(_meta.sys, "platform", "linux")
    assert _meta.uncommitted_targets(str(repo), ["Pricing.py"]) == []


def test_a_reverted_working_tree_edit_is_stale_until_head_moves(tmp_path: Path) -> None:
    repo, row = _state_repo(tmp_path)
    _record(repo, ["pricing.py"])

    out = _meta.freshness_from_repo(row, targets=["pricing.py", "billing.py"])
    assert "no longer in the working tree" in out["stale_warning"]
    assert "working_tree_dirty" not in out
    assert "stale_warning" not in _meta.freshness_from_repo(row, targets=["billing.py"])
    assert "stale_warning" not in _meta.freshness_from_repo(row, targets=None)

    # Still dirty means the dirty rule applies, not this one.
    (repo / "pricing.py").write_text(_EDITS["pricing.py"])
    _meta._dirty_paths_cache.clear()
    assert "uncommitted edits:" in _meta.freshness_from_repo(row, targets=["pricing.py"])[
        "stale_warning"
    ]
