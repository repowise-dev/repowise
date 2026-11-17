"""Calls through a configured base URL to a well-known third-party service."""

from __future__ import annotations

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.diagnostics import UnmatchedReason, build_diagnostics
from repowise.core.workspace.matching import annotate_consumer_targets
from repowise.core.workspace.matching.third_party import (
    THIRD_PARTY_SERVICES,
    mark_third_party_calls,
    third_party_service,
)


def _call(path: str, base_token: str | None, repo: str = "web") -> Contract:
    meta = {"method": "GET", "path": path}
    if base_token:
        meta["base_token"] = base_token
    return Contract(
        repo=repo,
        contract_id=f"http::GET::{path}",
        contract_type="http",
        role="consumer",
        file_path="src/lib/blog.ts",
        symbol_name="fetch",
        confidence=0.85,
        meta=meta,
    )


def test_every_row_declares_what_the_rule_needs():
    names = [row.name for row in THIRD_PARTY_SERVICES]
    assert names == sorted(names)
    assert len(set(names)) == len(names)
    for row in THIRD_PARTY_SERVICES:
        assert row.base_words and all(w == w.lower() and w.isalnum() for w in row.base_words)
        assert row.paths and all(p.startswith("/") for p in row.paths)


def test_a_supabase_base_and_api_path_name_supabase():
    assert third_party_service("SUPABASE_URL", "/rest/v1/content_topics") == "supabase"
    assert third_party_service("supabase_url", "/auth/v1/.well-known/jwks.json") == "supabase"
    assert third_party_service("supabaseUrl", "/storage/v1/object/x") == "supabase"


def test_both_the_base_and_the_path_must_agree():
    assert third_party_service("API_BASE", "/rest/v1/content_topics") is None
    assert third_party_service("SUPABASE_URL", "/api/posts") is None


def test_a_marked_call_reads_as_third_party_in_the_diagnostics():
    calls = [_call("/rest/v1/content_topics", "SUPABASE_URL"), _call("/rest/v1/x", None)]
    annotate_consumer_targets(calls)
    mark_third_party_calls(calls)
    reasons = {u.contract_id: u.reason for u in build_diagnostics(calls, []).unmatched_consumers}
    assert reasons == {
        "http::GET::/rest/v1/content_topics": UnmatchedReason.EXTERNAL_HOST,
        "http::GET::/rest/v1/x": UnmatchedReason.NO_PROVIDER,
    }


def test_a_base_the_workspace_places_is_left_alone():
    # A self-hosted copy that is a workspace member keeps its links.
    call = _call("/rest/v1/content_topics", "SUPABASE_URL")
    other = _call("/x", None, repo="supabase")
    annotate_consumer_targets([call, other], {"SUPABASE_URL": "supabase"})
    mark_third_party_calls([call, other])
    assert not call.meta.get("external")
    assert call.meta["target_repo"] == "supabase"
