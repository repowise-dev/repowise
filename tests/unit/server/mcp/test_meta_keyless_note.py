"""``get_answer``'s ``no-llm-provider`` degraded path names what a key would add,
and an index no model wrote prose for names the command that adds it (#3105).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from repowise.core.index_scope import answers_upgrade_line
from repowise.server.mcp_server import _meta
from repowise.server.mcp_server.tool_answer.answer import _degraded_payload

HITS = [{"target_path": "pkg/mod.py", "title": "mod", "summary": "s", "score": 1.0}]


def _repo(tmp_path: Path, docs_mode: str | None) -> Path:
    (tmp_path / ".repowise").mkdir()
    state = {"docs_mode": docs_mode} if docs_mode else {}
    (tmp_path / ".repowise" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return tmp_path


async def _payload(reason: str, repo: Path | None = None) -> dict:
    repository = SimpleNamespace(id=None, local_path=str(repo)) if repo else None
    return await _degraded_payload(
        reason=reason,
        note="DEGRADED",
        question="how does mod work",
        hits=HITS,
        fallback_targets=["pkg/mod.py"],
        repository=repository,
        t0=0.0,
    )


def test_a_no_prose_index_names_generate_and_the_restart(tmp_path: Path) -> None:
    note = _meta.keyless_note_meta(str(_repo(tmp_path, "deterministic")))["keyless_note"]
    assert "`repowise generate`" in note
    assert "no re-index" in note
    assert "restart the MCP server" in note


def test_an_index_only_repo_names_update_full(tmp_path: Path) -> None:
    note = _meta.keyless_note_meta(str(_repo(tmp_path, "none")))["keyless_note"]
    assert "`repowise update --full`" in note
    assert "no re-index" not in note


def test_model_written_prose_needs_only_the_key(tmp_path: Path) -> None:
    note = _meta.keyless_note_meta(str(_repo(tmp_path, "llm")))["keyless_note"]
    assert "repowise generate" not in note
    assert "restart the MCP server" in note


def test_overview_line_is_absent_once_a_model_wrote_the_prose(tmp_path: Path) -> None:
    assert _meta.prose_upgrade_meta(str(_repo(tmp_path, "llm"))) == {}
    assert _meta.prose_upgrade_meta(None) == {}


def test_overview_and_answer_share_one_line(tmp_path: Path) -> None:
    repo = str(_repo(tmp_path, "deterministic"))
    line = answers_upgrade_line(_meta.read_index_scope(repo))
    assert _meta.prose_upgrade_meta(repo) == {"keyless_note": line}
    assert _meta.keyless_note_meta(repo) == {"keyless_note": line}


async def test_every_no_llm_provider_response_carries_the_note(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "deterministic")
    first = await _payload("no-llm-provider", repo)
    second = await _payload("no-llm-provider", repo)
    assert "repowise generate" in first["_meta"]["keyless_note"]
    assert second["_meta"]["keyless_note"] == first["_meta"]["keyless_note"]


async def test_other_degraded_reasons_never_carry_the_note(tmp_path: Path) -> None:
    payload = await _payload("synthesis-failed", _repo(tmp_path, "deterministic"))
    assert "keyless_note" not in payload["_meta"]
