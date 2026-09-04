"""Shared MCP trust-envelope and registry evidence contracts."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.server.mcp_server import _meta


def test_build_meta_carries_contract_and_index_identity(tmp_path, monkeypatch):
    indexed = "a" * 40
    monkeypatch.setattr(_meta, "read_live_head", lambda _path: indexed)
    repository = SimpleNamespace(
        updated_at=None,
        local_path=str(tmp_path),
        head_commit=indexed,
    )

    meta = _meta.build_meta(repository=repository, targets=["src/a.py"])

    assert meta["contract_version"] == _meta.MCP_CONTRACT_VERSION
    assert meta["indexed_commit"] == indexed[:12]
    assert meta["live_head"] == indexed[:12]
    assert meta["index_behind"] is False


def test_shared_boundary_surfaces_degraded_partial_and_truncated_states():
    payload = {
        "degraded": "no-provider",
        "_meta": {
            "retrieval_degraded": ["vector"],
            "results_partial": True,
            "members_truncated": 2,
        },
    }

    result = _meta.finalize_trust_envelope(payload)

    assert result["_meta"]["state"] == {
        "degraded": True,
        # The umbrella bool cannot say which capability failed, so the reasons
        # behind it travel with it: the synthesis reason and the broken legs,
        # not just the names of the keys holding them.
        "degraded_reasons": {
            "degraded": "no-provider",
            "retrieval_degraded": ["vector"],
        },
        "partial": True,
        "truncated": True,
    }


def test_degraded_reasons_are_absent_when_nothing_degraded():
    result = _meta.finalize_trust_envelope({"_meta": {"members_truncated": 2}})

    assert result["_meta"]["state"] == {"truncated": True}


def test_persisted_analysis_metadata_omits_unknowns_and_preserves_commits():
    assert _meta.persisted_analysis_meta(None, {}) == {}
    assert _meta.persisted_analysis_meta("2026-08-24T00:00:00Z", {"api": "abc"}) == {
        "analysis_timestamp": "2026-08-24T00:00:00Z",
        "analysis_commits": {"api": "abc"},
    }


def test_structural_and_generated_evidence_cannot_masquerade_as_runtime_or_source():
    structural = _meta.finalize_trust_envelope({}, evidence_kind="structural")
    generated = _meta.finalize_trust_envelope({}, evidence_kind="generated")

    assert structural["_meta"]["evidence_kind"] == "structural"
    assert structural["_meta"]["runtime_breakage_proven"] is False
    assert generated["_meta"]["evidence_kind"] == "generated"
    assert generated["_meta"]["existing_verified_code"] is False


def test_every_registered_tool_has_a_valid_tier_and_specialist_trust_kinds():
    from repowise.core.registry import TOOL_TIERS, mcp_tool_registry
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()
    entries = mcp_tool_registry.entries()
    assert entries
    assert {entry.tier for entry in entries} <= TOOL_TIERS
    trust = {entry.name: entry.trust_kind for entry in entries}
    for name in (
        "get_architecture",
        "get_blast_radius",
        "get_dependency_path",
        "get_execution_flows",
        "get_conformance",
    ):
        assert trust[name] == "structural"
    assert trust["generate_refactoring_code"] == "generated"
