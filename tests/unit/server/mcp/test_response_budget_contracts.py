"""Golden contracts for the five shared-budget canonical MCP tools."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from repowise.core.distill.store import OmissionStore, default_store_path
from repowise.server.mcp_server import tool_middleware
from repowise.server.mcp_server._budget import (
    DEFAULT_RESPONSE_CHARS,
    EXPANDED_RESPONSE_CHARS,
    enforce_response_budget,
)


def _payload(tool: str, pad: int) -> dict[str, Any]:
    rows = [{"id": i, "evidence": f"EVIDENCE_{tool}_{i}_" + "x" * pad} for i in range(3)]
    meta = {"contract_version": 1, "indexed_commit": "a" * 12}
    if tool == "get_context":
        return {
            "targets": {
                "src/large.py": {
                    "target": "src/large.py",
                    "type": "file",
                    "docs": {"title": "Large", "content_md": rows[0]["evidence"], "symbols": rows},
                }
            },
            "_meta": meta,
        }
    if tool == "get_risk":
        return {
            "directive": {"tests_to_run": ["tests/test_large.py"], "summary": "review"},
            "targets": {"src/large.py": {"risk": "high", "summary": "inspect first"}},
            "pr_blast_radius": {"test_impact": rows, "evidence": rows[0]["evidence"]},
            "_meta": meta,
        }
    if tool == "get_change_risk":
        return {
            "classification": "elevated",
            "risk_percentile": 91.0,
            "fix_history": {"fix_count": 4, "files": rows},
            "prior_fixes": rows,
            "_meta": meta,
        }
    if tool == "get_answer":
        return {
            "answer": "The budget is applied after metadata.",
            "confidence": "high",
            "citations": ["src/large.py"],
            "retrieval": rows,
            "_meta": meta,
        }
    return {
        "title": "Large repository",
        "architecture": {"layers": [{"name": "Service", "file_count": 10}]},
        "tool_guide": {"details": rows[0]["evidence"]},
        "key_modules": rows,
        "_meta": meta,
    }


def _signature() -> inspect.Signature:
    def call(include: list[str] | None = None) -> None:
        pass

    return inspect.signature(call)


def _enforce(tool: str, payload: dict[str, Any], include: list[str] | None = None) -> dict:
    kwargs = {"include": include} if include is not None else {}
    return enforce_response_budget(
        tool,
        payload,
        signature=_signature(),
        args=(),
        kwargs=kwargs,
    )


@pytest.mark.parametrize(
    ("tool", "required"),
    [
        ("get_context", "targets"),
        ("get_risk", "directive"),
        ("get_change_risk", "classification"),
        ("get_answer", "answer"),
        ("get_overview", "title"),
    ],
)
@pytest.mark.parametrize(("case", "pad"), [("minimum", 0), ("typical", 600)])
def test_default_payload_goldens_keep_action_fields_and_exact_accounting(
    setup_mcp: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    required: str,
    case: str,
    pad: int,
) -> None:
    import repowise.server.mcp_server as mcp_mod

    repo = tmp_path / case / tool
    (repo / ".repowise").mkdir(parents=True)
    monkeypatch.setattr(mcp_mod, "_repo_path", str(repo))
    result = _enforce(tool, _payload(tool, pad))

    accounting = result["_meta"]["response_budget"]
    assert required in result
    assert accounting == {
        "limit_chars": DEFAULT_RESPONSE_CHARS,
        "tier": "default",
        "serialized_chars": len(json.dumps(result, separators=(",", ":"), default=str)),
    }
    assert "truncated" not in result


@pytest.mark.parametrize(
    ("tool", "required"),
    [
        ("get_context", "targets"),
        ("get_risk", "directive"),
        ("get_change_risk", "classification"),
        ("get_answer", "answer"),
        ("get_overview", "title"),
    ],
)
def test_adversarial_payload_goldens_fit_and_recover_in_one_lookup(
    setup_mcp: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    required: str,
) -> None:
    import repowise.server.mcp_server as mcp_mod

    repo = tmp_path / tool
    (repo / ".repowise").mkdir(parents=True)
    monkeypatch.setattr(mcp_mod, "_repo_path", str(repo))
    result = _enforce(tool, _payload(tool, 15_000))
    size = len(json.dumps(result, separators=(",", ":"), default=str))

    accounting = result["_meta"]["response_budget"]
    assert required in result
    assert size == accounting["serialized_chars"] <= accounting["limit_chars"]
    assert accounting["limit_chars"] == DEFAULT_RESPONSE_CHARS
    assert "enforcement_error" not in accounting
    assert result["truncated"] is True
    refs = result["_meta"]["omitted"]["refs"]
    assert refs

    store = OmissionStore(default_store_path(repo))
    try:
        recovered = store.get(refs[-1])
    finally:
        store.close()
    assert recovered is not None and f"EVIDENCE_{tool}" in recovered


def test_explicit_include_uses_the_expansion_tier(
    setup_mcp: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))
    result = _enforce("get_overview", _payload("get_overview", 8_000), ["content"])
    accounting = result["_meta"]["response_budget"]
    assert accounting["tier"] == "expanded"
    assert accounting["limit_chars"] == EXPANDED_RESPONSE_CHARS
    assert accounting["serialized_chars"] <= EXPANDED_RESPONSE_CHARS


def test_oversized_protected_answer_is_reduced_and_recoverable(
    setup_mcp: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))
    payload = _payload("get_answer", 0)
    payload["answer"] = "PROTECTED_ANSWER_" + "z" * 50_000
    result = _enforce("get_answer", payload)

    accounting = result["_meta"]["response_budget"]
    assert result["answer"].startswith("PROTECTED_ANSWER_")
    assert accounting["serialized_chars"] <= DEFAULT_RESPONSE_CHARS
    assert "enforcement_error" not in accounting
    refs = result["_meta"]["omitted"]["refs"]
    store = OmissionStore(default_store_path(tmp_path))
    try:
        assert any("PROTECTED_ANSWER_" in (store.get(ref) or "") for ref in refs)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_middleware_budgets_after_trust_metadata(
    setup_mcp: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))

    async def get_risk() -> dict:
        return _payload("get_risk", 15_000)

    result = await tool_middleware(get_risk)()
    assert result["_meta"]["contract_version"] == 1
    accounting = result["_meta"]["response_budget"]
    assert accounting["serialized_chars"] == len(
        json.dumps(result, separators=(",", ":"), default=str)
    )
    assert accounting["serialized_chars"] <= DEFAULT_RESPONSE_CHARS
