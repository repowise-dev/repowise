"""Tests for the shared MCP budgeter and reversible truncation.

Covers:
* OmissionCollector — round-trip through the omission store, inline markers,
  and the degrade-to-silent-drop posture on store failure.
* truncate_to_budget — dropped symbols / targets / skeleton texts are
  recoverable, and keep/drop decisions are unchanged by the collector.
* get_symbol — omission-ref overload (``repowise#<12-hex>``), query
  filtering, and byte-identical normal symbol resolution.
* Migrated tools (get_dead_code, get_risk trim helper, get_overview) — no
  silent truncation: every drop yields ``_meta.omitted`` refs that expand.
* CLI round-trip — ``repowise expand`` resolves MCP-origin rows.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from repowise.core.distill.markers import MARKER_RE
from repowise.core.distill.store import OmissionStore, default_store_path
from repowise.server.mcp_server._budget import OmissionCollector, truncate_to_budget


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """A fake repo that has opted into repowise (so the store is repo-local)."""
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def _store_get(repo: Path, ref: str, query: str | None = None) -> str | None:
    store = OmissionStore(default_store_path(repo))
    try:
        return store.get(ref, query=query)
    finally:
        store.close()


def _store_record(repo: Path, ref: str) -> dict | None:
    store = OmissionStore(default_store_path(repo))
    try:
        return store.get_record(ref)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# OmissionCollector
# ---------------------------------------------------------------------------


def test_collector_combined_doc_round_trip(repo_root: Path):
    collector = OmissionCollector("test_tool", repo_root=repo_root)
    collector.add("first chunk", "alpha line one\nalpha line two")
    collector.add("second chunk", [{"file_path": "x.py", "count": 3}])

    response: dict = {"_meta": {"timing_ms": 1.0}}
    collector.attach(response)

    marker = response["omission_marker"]
    assert MARKER_RE.fullmatch(marker)
    omitted = response["_meta"]["omitted"]
    assert omitted["refs"] and omitted["tokens"] > 0
    assert "get_symbol" in omitted["restore"] and "repowise expand" in omitted["restore"]

    ref = omitted["refs"][0]
    content = _store_get(repo_root, ref)
    assert content is not None
    assert "alpha line one" in content
    assert "first chunk" in content and "second chunk" in content
    assert '"file_path": "x.py"' in content
    assert _store_record(repo_root, ref)["source"] == "mcp:test_tool"


def test_collector_inline_marker_is_byte_identical(repo_root: Path):
    collector = OmissionCollector("test_tool", repo_root=repo_root)
    original = "def f():\n    return 1\n\n# tail"
    marker = collector.add_inline("skeleton of x.py", original)
    assert marker is not None and MARKER_RE.fullmatch(marker)

    response: dict = {}
    collector.attach(response)
    refs = response["_meta"]["omitted"]["refs"]
    assert len(refs) == 1
    # Inline rows store the content alone — expand returns it verbatim.
    assert _store_get(repo_root, refs[0]) == original
    # No combined doc was queued, so no top-level marker.
    assert "omission_marker" not in response


def test_collector_store_failure_degrades_silently(tmp_path: Path):
    # A *file* where the store's parent directory should be makes every
    # write fail; the collector must degrade to silent-drop, never raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    collector = OmissionCollector("test_tool", store_path=blocker / "omissions.db")

    assert collector.add_inline("label", "content") is None
    collector.add("label", "queued content")
    response: dict = {"_meta": {}}
    collector.attach(response)
    assert "omission_marker" not in response
    assert "omitted" not in response["_meta"]


# ---------------------------------------------------------------------------
# truncate_to_budget with a collector
# ---------------------------------------------------------------------------


def _big_response(n_targets: int = 4, n_symbols: int = 40) -> dict:
    targets = {}
    for i in range(n_targets):
        name = f"pkg/mod_{i}/file_{i}.py"
        targets[name] = {
            "target": name,
            "type": "file",
            "docs": {
                "title": f"File {i}",
                "content_md": f"BODY_{i} " + "x" * 2000,
                "symbols": [
                    {
                        "name": f"Sym{i}_{j}",
                        "kind": "function",
                        "signature": f"def sym_{j}(...)",
                        "docstring": "d" * 200,
                    }
                    for j in range(n_symbols)
                ],
            },
        }
    return {"targets": targets, "_meta": {"timing_ms": 1.0}}


def test_budgeter_dropped_content_recoverable(repo_root: Path):
    response = _big_response()
    collector = OmissionCollector("get_context", repo_root=repo_root)
    out = truncate_to_budget(response, char_budget=4000, collector=collector)

    assert out["truncated"] is True
    omitted = out["_meta"]["omitted"]
    assert omitted["refs"]
    assert MARKER_RE.fullmatch(out["omission_marker"])

    # Everything reported as dropped must be findable in the stored doc(s).
    stored = "\n".join(_store_get(repo_root, r) or "" for r in omitted["refs"])
    for tgt_name in out["dropped_targets"]:
        assert f"dropped target {tgt_name}" in stored
    for names in out["dropped_symbols"].values():
        for name in names:
            assert name in stored
    # Stage-1 heavy fields are captured too.
    assert "BODY_" in stored


def test_budgeter_decisions_unchanged_by_collector(repo_root: Path):
    fixture = _big_response()
    plain = truncate_to_budget(copy.deepcopy(fixture), char_budget=4000)
    collector = OmissionCollector("get_context", repo_root=repo_root)
    collected = truncate_to_budget(copy.deepcopy(fixture), char_budget=4000, collector=collector)

    # Strip the additive reversibility fields; the rest must be identical.
    collected.pop("omission_marker", None)
    collected["_meta"].pop("omitted", None)
    assert json.dumps(collected, sort_keys=True, default=str) == json.dumps(
        plain, sort_keys=True, default=str
    )


def _skeleton_response(text_chars: int = 12000) -> dict:
    return {
        "targets": {
            "pkg/big.py": {
                "target": "pkg/big.py",
                "type": "file",
                "docs": {"title": "Big", "symbols": [{"name": "f", "kind": "function"}]},
                "skeleton": {
                    "mode": "smart",
                    "tokens": text_chars // 4,
                    "full_tokens": text_chars,
                    "text": "line\n" * (text_chars // 5),
                },
            }
        },
        "_meta": {},
    }


def test_skeleton_stage_replaces_text_with_marker(repo_root: Path):
    response = _skeleton_response()
    original = response["targets"]["pkg/big.py"]["skeleton"]["text"]
    collector = OmissionCollector("get_context", repo_root=repo_root)
    out = truncate_to_budget(response, char_budget=2000, collector=collector)

    skel = out["targets"]["pkg/big.py"]["skeleton"]
    assert skel["omitted"] is True
    assert MARKER_RE.fullmatch(skel["text"])
    assert out["truncated"] is True
    ref = MARKER_RE.fullmatch(skel["text"]).group("ref")
    assert _store_get(repo_root, ref) == original


def test_skeleton_stage_without_collector_drops_with_note():
    out = truncate_to_budget(_skeleton_response(), char_budget=2000)
    skel = out["targets"]["pkg/big.py"]["skeleton"]
    assert "text" not in skel
    assert skel["omitted"] is True
    assert "budget" in skel["note"]


# ---------------------------------------------------------------------------
# get_symbol — omission-ref overload + normal-path stability
# ---------------------------------------------------------------------------


def _put_row(repo: Path, content: str, source: str = "mcp:test") -> str:
    store = OmissionStore(default_store_path(repo))
    try:
        return store.put(content, source=source, original_tokens=100, kept_tokens=0)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_get_symbol_resolves_omission_ref(setup_mcp, repo_root: Path):
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import get_symbol

    mcp_mod._repo_path = str(repo_root)
    content = "ERROR first\nok line\nERROR second"
    ref = _put_row(repo_root, content)

    result = await get_symbol(f"repowise#{ref}")
    assert result["kind"] == "omission"
    assert result["content"] == content
    assert result["source"] == "mcp:test"
    assert result["ref"] == ref
    assert "created_at" in result

    filtered = await get_symbol(f"repowise#{ref}", query="^ERROR")
    assert filtered["content"] == "ERROR first\nERROR second"
    assert filtered["query"] == "^ERROR"


@pytest.mark.asyncio
async def test_get_symbol_accepts_pasted_marker(setup_mcp, repo_root: Path):
    import repowise.server.mcp_server as mcp_mod
    from repowise.core.distill.markers import render_marker
    from repowise.server.mcp_server import get_symbol

    mcp_mod._repo_path = str(repo_root)
    ref = _put_row(repo_root, "stashed content")
    marker = render_marker(ref, 1, 3)

    result = await get_symbol(marker)
    assert result["content"] == "stashed content"


@pytest.mark.asyncio
async def test_get_symbol_unknown_ref_errors(setup_mcp, repo_root: Path):
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import get_symbol

    mcp_mod._repo_path = str(repo_root)
    result = await get_symbol("repowise#" + "0" * 12)
    assert "error" in result
    assert "expired" in result["error"]


@pytest.mark.asyncio
async def test_get_symbol_normal_path_byte_identical(setup_mcp, repo_root: Path):
    """A real symbol_id must still slice the exact on-disk byte range."""
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import get_symbol

    src = repo_root / "src" / "auth"
    src.mkdir(parents=True)
    lines = [f"line {i}" for i in range(1, 121)]
    (src / "service.py").write_text("\n".join(lines), encoding="utf-8")
    mcp_mod._repo_path = str(repo_root)

    # WikiSymbol fixture rows: login spans lines 20..40 (see conftest).
    result = await get_symbol("src/auth/service.py::login")
    assert result["source"] == "\n".join(lines[19:40])
    assert result["start_line"] == 20 and result["end_line"] == 40
    assert result["truncated"] is False


# ---------------------------------------------------------------------------
# Migrated tools — no silent truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dead_code_truncation_is_expandable(setup_mcp, repo_root: Path):
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import get_dead_code

    mcp_mod._repo_path = str(repo_root)
    # Fixture has two medium-tier findings; limit=1 drops one of them.
    result = await get_dead_code(limit=1)
    assert result["tiers"]["medium"]["truncated"] is True
    omitted = result["_meta"]["omitted"]
    assert omitted["refs"]

    stored = "\n".join(_store_get(repo_root, r) or "" for r in omitted["refs"])
    assert "OldModel" in stored  # the dropped medium finding
    assert "medium-tier findings beyond limit=1" in stored
    rec = _store_record(repo_root, omitted["refs"][0])
    assert rec["source"] == "mcp:get_dead_code"


def test_risk_trim_blast_lists_collects_drops(repo_root: Path):
    from repowise.server.mcp_server.tool_risk import _trim_blast_lists

    blast = {
        "transitive_affected": [f"pkg/f{i}.py" for i in range(20)],
        "overall_risk_score": 4.2,
    }
    collector = OmissionCollector("get_risk", repo_root=repo_root)
    trimmed = _trim_blast_lists(blast, None, collector)
    assert len(trimmed["transitive_affected"]) == 15
    assert trimmed["transitive_affected_truncated_total"] == 20

    response: dict = {"_meta": {}}
    collector.attach(response)
    refs = response["_meta"]["omitted"]["refs"]
    stored = _store_get(repo_root, refs[0])
    assert "pkg/f19.py" in stored and "pkg/f15.py" in stored
    assert "pkg/f14.py" not in stored  # kept entries are not stored


@pytest.mark.asyncio
async def test_get_overview_module_cap_is_expandable(setup_mcp, session, repo_root: Path):
    from datetime import UTC, datetime

    import repowise.server.mcp_server as mcp_mod
    from repowise.core.persistence.models import Page
    from repowise.server.mcp_server import get_overview

    now = datetime(2026, 3, 19, tzinfo=UTC)
    for i in range(25):
        session.add(
            Page(
                id=f"module_page:src/extra{i:02d}",
                repository_id=setup_mcp,
                page_type="module_page",
                title=f"Extra Module {i:02d}",
                content=f"# Extra Module {i:02d}\n\nFiller.",
                target_path=f"src/extra{i:02d}",
                source_hash=f"extra{i}",
                model_name="mock",
                provider_name="mock",
                generation_level=4,
                confidence=0.9,
                freshness_status="fresh",
                metadata_json="{}",
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()

    mcp_mod._repo_path = str(repo_root)
    result = await get_overview()
    assert len(result["key_modules"]) == 20
    omitted = result["_meta"]["omitted"]
    stored = "\n".join(_store_get(repo_root, r) or "" for r in omitted["refs"])
    # 27 module pages total, ordered by title — the tail is in the store.
    assert "module pages beyond cap=20" in stored
    assert stored.count("Extra Module") + stored.count("Module") >= 7


# ---------------------------------------------------------------------------
# Cross-surface round-trip — CLI expand resolves MCP-origin rows
# ---------------------------------------------------------------------------


def test_cli_expand_resolves_mcp_row(repo_root: Path, monkeypatch):
    from click.testing import CliRunner

    from repowise.cli.commands.expand_cmd import expand_command

    collector = OmissionCollector("get_context", repo_root=repo_root)
    collector.add("dropped target pkg/a.py", {"target": "pkg/a.py", "docs": {"title": "A"}})
    response: dict = {"_meta": {}}
    collector.attach(response)
    ref = response["_meta"]["omitted"]["refs"][0]

    monkeypatch.chdir(repo_root)
    result = CliRunner().invoke(expand_command, [ref])
    assert result.exit_code == 0, result.output
    assert "dropped target pkg/a.py" in result.output
    assert '"title": "A"' in result.output
