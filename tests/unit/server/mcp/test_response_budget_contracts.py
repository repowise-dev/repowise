"""Golden contracts for the shared-budget canonical MCP tools."""

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
    resolve_response_budget_repo_root,
)


def _payload(tool: str, pad: int) -> dict[str, Any]:
    if tool == "get_health":
        pad = min(pad, 10_000)
    if tool == "get_why":
        pad = min(pad, 9_000)
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
    if tool == "get_why":
        decisions = [
            {
                "id": row["id"],
                "title": f"Decision {row['id']}",
                "rationale": row["evidence"],
                "provenance": "historical",
                "evidence_refs": [
                    {
                        "id": f"ev_{row['id']}",
                        "repository": "test-repo",
                        "kind": "commit",
                        "commit": f"{row['id']:040x}",
                    }
                ],
            }
            for row in rows
        ]
        return {
            "mode": "search",
            "query": "why is the response bounded",
            "decisions": decisions,
            "_meta": meta,
        }
    if tool == "get_health":
        return {
            "mode": "dashboard",
            "directive": {"fix_first": "src/large.py", "reason": "highest leverage"},
            "kpis": {"average_health": 4.2, "file_count": 3},
            "high_leverage_files": rows,
            "high_leverage_files_total": len(rows),
            "high_leverage_files_emitted": len(rows),
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
    # A tool registers its budget hooks when its module loads, which in
    # production has always happened by the time the middleware calls through.
    # Loading the surface is what makes a direct call mirror that.
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()
    kwargs = {"include": include} if include is not None else {}
    return enforce_response_budget(
        tool,
        payload,
        signature=_signature(),
        args=(),
        kwargs=kwargs,
    )


def test_health_plan_status_tracks_final_budget_removal(setup_mcp: str) -> None:
    payload = {
        "mode": "dashboard",
        "directive": None,
        "kpis": {"average_health": 7.0},
        "refactoring_plans": [{"id": "plan_1", "evidence": "x" * 30_000}],
        "refactoring_plans_total": 1,
        "refactoring_plans_status": {"state": "available", "reason": None},
        "_meta": {"contract_version": 1},
    }

    result = _enforce("get_health", payload)

    assert result.get("refactoring_plans", []) == []
    assert result["refactoring_plans_total"] == 1
    assert result["refactoring_plans_status"]["state"] == "available_not_emitted"
    assert result["refactoring_plans_status"]["reason"] == "response_budget"


@pytest.mark.parametrize(
    ("tool", "required"),
    [
        ("get_context", "targets"),
        ("get_risk", "directive"),
        ("get_change_risk", "classification"),
        ("get_answer", "answer"),
        ("get_why", "query"),
        ("get_overview", "title"),
        ("get_health", "directive"),
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
        ("get_why", "query"),
        ("get_overview", "title"),
        ("get_health", "directive"),
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
    if tool == "get_health":
        assert len(refs) == 1
    if tool == "get_risk":
        reductions = result["_meta"].get("reductions", [])
        assert reductions and all(
            row["total"] >= row["emitted"] and row["reason"] == "response_budget"
            for row in reductions
        )
    elif tool == "get_change_risk":
        # Diff-shape history sheds before the action blocks: fix_history's rows
        # go first, and the directive/health_delta never do.
        assert result["fix_history"]["files_total"] == 3
        assert result["fix_history"]["files_emitted"] < 3
        assert result["fix_history"]["files_reduced_reason"] == "response_budget"
    elif tool == "get_health":
        assert result["high_leverage_files_total"] == 3
        assert result["high_leverage_files_emitted"] < 3
        assert result["high_leverage_files_reduced_reason"] == "response_budget"

    store = OmissionStore(default_store_path(repo))
    try:
        recovered = [store.get(ref) for ref in refs]
    finally:
        store.close()
    assert any(value is not None and f"EVIDENCE_{tool}" in value for value in recovered)
    if tool == "get_why":
        assert len(refs) == 1
        recovered_payload = recovered[0] or ""
        emitted_ids = {row["id"] for row in result["decisions"]}
        for row_id in set(range(3)) - emitted_ids:
            assert f'"id": {row_id}' in recovered_payload
            assert f'"id": "ev_{row_id}"' in recovered_payload
        for row_id in emitted_ids:
            assert f'"id": {row_id}' not in recovered_payload
    if tool == "get_health":
        joined = recovered[0] or ""
        emitted_ids = {row["id"] for row in result["high_leverage_files"]}
        for row_id in set(range(3)) - emitted_ids:
            assert f'"id": {row_id}' in joined
        for row_id in emitted_ids:
            assert f'"id": {row_id}' not in joined


def test_health_budget_prunes_profiles_for_removed_plans(
    setup_mcp: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))
    plans = [
        {"id": f"plan-{index}", "validation_profile_id": f"profile-{index}"}
        for index in range(8)
    ]
    profiles = [
        {
            "id": f"profile-{index}",
            "commands": [f"pytest tests/test_{index}.py " + "x" * 6_000],
        }
        for index in range(8)
    ]
    payload = {
        "mode": "targets",
        "targets": ["src/large.py"],
        "refactoring_plans": plans,
        "refactoring_plans_total": len(plans),
        "validation_profiles": profiles,
        "validation_profiles_total": len(profiles),
        "_meta": {"contract_version": 1},
    }

    result = _enforce("get_health", payload, ["refactoring"])
    referenced = {plan["validation_profile_id"] for plan in result["refactoring_plans"]}
    retained = {profile["id"] for profile in result["validation_profiles"]}
    assert retained == referenced
    assert result["validation_profiles_emitted"] == len(retained)
    assert result["validation_profiles_reduced_reason"] == "response_budget"
    assert result["_meta"]["response_budget"]["serialized_chars"] <= EXPANDED_RESPONSE_CHARS


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
    assert result["_meta"]["state"]["truncated"] is True
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


@pytest.mark.asyncio
async def test_get_why_middleware_accounts_after_trust_and_keeps_refs_recoverable(
    setup_mcp: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))

    async def get_why() -> dict:
        return _payload("get_why", 15_000)

    result = await tool_middleware(get_why)()
    accounting = result["_meta"]["response_budget"]
    assert result["_meta"]["contract_version"] == 1
    assert accounting["serialized_chars"] == len(
        json.dumps(result, separators=(",", ":"), default=str)
    )
    assert accounting["serialized_chars"] <= DEFAULT_RESPONSE_CHARS
    assert all(row["evidence_refs"] for row in result["decisions"])
    assert len(result["_meta"]["omitted"]["refs"]) == 1


@pytest.mark.asyncio
async def test_budget_repo_root_follows_workspace_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import repowise.server.mcp_server._helpers as helpers

    selected = tmp_path / "selected"

    async def resolve(repo: str | None = None) -> Any:
        assert repo == "other"
        return SimpleNamespace(path=selected)

    monkeypatch.setattr(helpers, "_resolve_repo_context", resolve)

    def call(repo: str | None = None) -> None:
        pass

    signature = inspect.signature(call)
    assert await resolve_response_budget_repo_root(signature, (), {"repo": "other"}) == selected


def test_every_registered_tool_declares_a_contract() -> None:
    """No tool may fall through the shared layer and be delivered unbounded."""
    from repowise.server.mcp_server import _TOOL_MODULES
    from repowise.server.mcp_server._budget import budgeted_tool_names

    assert set(_TOOL_MODULES) == set(budgeted_tool_names())


def test_no_contract_sheds_a_block_it_also_protects() -> None:
    """Removing a whole top-level block the guard protects is a contradiction.

    Trimming a protected block's tail (``targets[]`` under ``targets``) is not:
    ``fit_to_budget`` ranks rows, ``protected`` only stops wholesale removal.
    """
    from repowise.server.mcp_server._budget.contracts import _CONTRACTS

    for tool, contract in _CONTRACTS.items():
        whole_blocks = {
            key for key in contract.shed_order if "." not in key and not key.endswith("[]")
        }
        assert not whole_blocks & set(contract.protected), tool


def test_every_hook_owner_registers_on_tool_import() -> None:
    """A hook lost at import is a silent correctness bug, so pin the owners."""
    from repowise.server.mcp_server import ensure_full_surface
    from repowise.server.mcp_server._budget import registered_hook_tools

    ensure_full_surface()
    assert {"get_health", "get_why", "search_codebase"} <= registered_hook_tools()


def test_an_undeclared_tool_is_still_bounded_and_says_so(setup_mcp: str) -> None:
    """The floor, for a name the registry does not know."""
    payload = {"body": "x" * 60_000, "_meta": {"contract_version": 1}}

    result = _enforce("not_a_registered_tool", payload)

    assert len(json.dumps(result, separators=(",", ":"), default=str)) <= (
        result["_meta"]["response_budget"]["limit_chars"]
    )
    assert result["truncated"] is True
    assert result["_meta"]["omitted"]["refs"]
    assert "repowise expand" in result["_meta"]["omitted"]["restore"]


def test_oversized_symbol_read_sheds_callees_before_the_body(setup_mcp: str) -> None:
    """get_symbol's measured worst case: callee context goes before the subject."""
    payload = {
        "symbol_id": "src/hub.py::Hub",
        "file": "src/hub.py",
        "name": "Hub",
        "verified": True,
        "continuation": "src/hub.py:601-1200",
        "source": "BODY" * 4_000,
        "callee_bodies": [{"symbol_id": f"src/c.py::c{i}", "source": "C" * 4_000} for i in range(8)],
        "_meta": {"contract_version": 1},
    }

    result = _enforce("get_symbol", payload)

    assert result["symbol_id"] == "src/hub.py::Hub"
    assert result["continuation"] == "src/hub.py:601-1200"
    assert len(result.get("callee_bodies") or []) < 8
    assert result["truncated"] is True
    assert result["_meta"]["omitted"]["refs"]


def test_an_oversized_body_is_trimmed_to_fit_not_deleted(setup_mcp: str) -> None:
    """A symbol read with no body is a wasted call, even with a recoverable ref."""
    payload = {
        "symbol_id": "src/hub.py::Hub",
        "file": "src/hub.py",
        "continuation": "src/hub.py:601-1200",
        "source": "BODY" * 9_000,
        "_meta": {"contract_version": 1},
    }

    result = _enforce("get_symbol", payload)

    limit = result["_meta"]["response_budget"]["limit_chars"]
    assert len(json.dumps(result, separators=(",", ":"), default=str)) <= limit
    # Most of the budget goes to the body rather than to a bare pointer.
    assert len(result["source"]) > limit // 2
    assert result["truncated"] is True
    assert result["_meta"]["omitted"]["refs"]


def test_budget_hooks_run_for_the_tool_that_registered_them() -> None:
    """A hook is keyed by tool, so it must not fire for another tool's response."""
    from repowise.server.mcp_server._budget import hooks

    seen: list[str] = []

    def hook(result: dict[str, Any]) -> None:
        seen.append(result["tool"])

    hooks.register_post_enforce("tool_a", hook)
    try:
        hooks.run_post_enforce("tool_a", {"tool": "tool_a"})
        hooks.run_post_enforce("tool_b", {"tool": "tool_b"})
    finally:
        hooks._POST_ENFORCE.pop("tool_a", None)

    assert seen == ["tool_a"]


#: Real response shapes, recorded by ``scripts/measure_mcp_response_sizes.py``
#: against an indexed repository. Regenerate with ``--skeletons`` when a tool's
#: payload changes shape.
_SHAPES_FIXTURE = (
    Path(__file__).resolve().parents[3] / "fixtures" / "mcp" / "tool_response_shapes.json"
)


def _resolve_shed_path(shape: dict[str, Any], key: str) -> str | None:
    """Return why *key* fails to name a real block, or None when it resolves.

    A block absent from the recording is not a failure: several are conditional
    on arguments the recording did not pass. A block whose *parent* is present
    while the leaf is not is a typo, and a silent no-op in ``fit_to_budget``.
    """
    leaf, tail = (key[:-2], True) if key.endswith("[]") else (key, False)
    *parents, name = leaf.split(".")
    node: Any = shape
    for parent in parents:
        if not isinstance(node, dict) or parent not in node:
            return None
        node = node[parent]
    if not isinstance(node, dict):
        return f"{'.'.join(parents)} is not a block"
    if name not in node:
        if not parents:
            return None
        return f"{leaf} does not exist; {'.'.join(parents)} has {sorted(node)}"
    if tail and not isinstance(node[name], (list, dict)):
        return f"{leaf} is not a collection, so a tail trim cannot apply"
    return None


def test_every_shed_path_names_a_real_block() -> None:
    """A shed path that does not resolve sheds nothing, silently.

    The size columns cannot catch this: the final guard brings the response
    under the ceiling either way, so a dead order measures as a working one.

    Bound worth knowing: this catches a leaf misspelled under a parent the
    recording contains. A top-level block the recording never exercised is
    skipped, because most tools have blocks conditional on arguments the sweep
    does not pass, and failing on those would be noise rather than signal.
    """
    from repowise.server.mcp_server._budget.contracts import _CONTRACTS

    shapes = json.loads(_SHAPES_FIXTURE.read_text(encoding="utf-8"))
    problems: list[str] = []
    for tool, contract in _CONTRACTS.items():
        shape = shapes.get(tool)
        if shape is None:
            continue
        problems += [
            f"{tool}: {key} -- {reason}"
            for key in contract.shed_order
            if (reason := _resolve_shed_path(shape, key))
        ]
    assert not problems, "\n".join(problems)


def test_recorded_shapes_cover_the_tools_that_can_be_exercised() -> None:
    """Guard the fixture itself: a shrinking recording weakens the check above."""
    from repowise.server.mcp_server import _TOOL_MODULES

    shapes = json.loads(_SHAPES_FIXTURE.read_text(encoding="utf-8"))
    # generate_refactoring_code needs a stored plan, which the recorded index
    # had none of. Reported as not measured rather than assumed bounded.
    assert set(shapes) == set(_TOOL_MODULES) - {"generate_refactoring_code"}
