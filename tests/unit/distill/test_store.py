"""Unit tests for the OmissionStore sidecar."""

from __future__ import annotations

import time
from pathlib import Path

from repowise.core.distill.store import OmissionStore, content_ref, default_store_path


def test_put_get_roundtrip(store: OmissionStore) -> None:
    content = "line one\nline two with unicode ✓\nline three"
    ref = store.put(content, source="cli:test_output", original_tokens=100, kept_tokens=20)
    assert len(ref) == 12
    assert store.get(ref) == content


def test_put_is_idempotent_per_content(store: OmissionStore) -> None:
    a = store.put("same", source="cli:logs", original_tokens=10, kept_tokens=2)
    b = store.put("same", source="cli:logs", original_tokens=10, kept_tokens=2)
    assert a == b == content_ref("same")


def test_get_unknown_ref_returns_none(store: OmissionStore) -> None:
    assert store.get("0" * 12) is None
    assert store.get("not-a-ref") is None


def test_get_record_returns_provenance(store: OmissionStore) -> None:
    before = time.time()
    ref = store.put(
        "payload\nERROR boom", source="mcp:get_context", original_tokens=50, kept_tokens=5
    )
    record = store.get_record(ref)
    assert record["content"] == "payload\nERROR boom"
    assert record["source"] == "mcp:get_context"
    assert record["original_tokens"] == 50
    assert record["kept_tokens"] == 5
    assert record["created_at"] >= before
    # Query filtering applies to the record's content too.
    assert store.get_record(ref, query="^ERROR")["content"] == "ERROR boom"
    assert store.get_record("0" * 12) is None


def test_get_with_query_filters_lines(store: OmissionStore) -> None:
    content = "FAILED test_a\npassed test_b\nFAILED test_c"
    ref = store.put(content, source="cli:test_output", original_tokens=10, kept_tokens=2)
    assert store.get(ref, query="FAILED") == "FAILED test_a\nFAILED test_c"


def test_get_with_invalid_regex_falls_back_to_substring(store: OmissionStore) -> None:
    content = "a [bracket( line\nother"
    ref = store.put(content, source="cli:logs", original_tokens=10, kept_tokens=2)
    assert store.get(ref, query="[bracket(") == "a [bracket( line"


def test_access_count_increments(store: OmissionStore) -> None:
    ref = store.put("content", source="cli:logs", original_tokens=10, kept_tokens=2)
    store.get(ref)
    store.get(ref)
    count = store._conn.execute(
        "SELECT access_count FROM omissions WHERE ref = ?", (ref,)
    ).fetchone()[0]
    assert count == 2


def test_ttl_prune_drops_old_rows(tmp_path: Path) -> None:
    s = OmissionStore(tmp_path / "omissions.db", ttl_days=1)
    ref = s.put("old content", source="cli:logs", original_tokens=10, kept_tokens=2)
    # Backdate the row past the TTL, then trigger an opportunistic prune.
    s._conn.execute(
        "UPDATE omissions SET created_at = ? WHERE ref = ?",
        (time.time() - 2 * 86400, ref),
    )
    s._conn.commit()
    s.prune()
    assert s.get(ref) is None
    s.close()


def test_size_cap_prunes_oldest_first(tmp_path: Path) -> None:
    # Cap small enough that two large random-ish payloads cannot coexist.
    s = OmissionStore(tmp_path / "omissions.db", max_mb=0.001)  # 1 KB
    import random

    rng = random.Random(42)
    blob_a = "".join(chr(rng.randint(33, 126)) for _ in range(4000))
    blob_b = "".join(chr(rng.randint(33, 126)) for _ in range(4000))
    ref_a = s.put(blob_a, source="cli:logs", original_tokens=10, kept_tokens=2)
    s._conn.execute("UPDATE omissions SET created_at = created_at - 60 WHERE ref = ?", (ref_a,))
    s._conn.commit()
    ref_b = s.put(blob_b, source="cli:logs", original_tokens=10, kept_tokens=2)
    assert s.get(ref_a) is None  # oldest evicted
    s.close()
    assert ref_b != ref_a


def test_savings_ledger_roundtrip(store: OmissionStore) -> None:
    store.record_saving(
        filter_name="test_output",
        source="cli",
        command="pytest",
        raw_tokens=1000,
        distilled_tokens=100,
    )
    store.record_saving(
        filter_name="git_log",
        source="cli",
        command="git log",
        raw_tokens=500,
        distilled_tokens=50,
    )
    summary = store.savings_summary()
    assert summary["events"] == 2
    assert summary["raw_tokens"] == 1500
    assert summary["saved_tokens"] == 1350
    assert summary["per_filter"]["test_output"]["saved_tokens"] == 900


def test_mcp_drops_summary_reads_omissions(store: OmissionStore) -> None:
    from repowise.core.distill.tracking import mcp_drops_summary

    # MCP truncation drops land in the omissions table (kept_tokens=0), never
    # in the savings ledger.
    store.put("a" * 400, source="mcp:get_risk", original_tokens=300, kept_tokens=0)
    store.put("b" * 400, source="mcp:get_risk", original_tokens=200, kept_tokens=0)
    store.put("c" * 400, source="mcp:get_overview", original_tokens=100, kept_tokens=0)
    # Distill omissions must not leak into the MCP view.
    store.put("d" * 400, source="cli:test_output", original_tokens=999, kept_tokens=50)

    summary = mcp_drops_summary(store._conn)
    assert summary["events"] == 3
    assert summary["tokens"] == 600
    assert summary["per_tool"]["get_risk"] == {"events": 2, "tokens": 500}
    assert summary["per_tool"]["get_overview"] == {"events": 1, "tokens": 100}
    # Ordered by tokens desc — biggest tool first.
    assert list(summary["per_tool"]) == ["get_risk", "get_overview"]


def test_mcp_drops_summary_empty(store: OmissionStore) -> None:
    from repowise.core.distill.tracking import mcp_drops_summary

    summary = mcp_drops_summary(store._conn)
    assert summary == {"events": 0, "tokens": 0, "per_tool": {}}


def test_distill_summary_excludes_mcp_rows(store: OmissionStore) -> None:
    from repowise.core.distill.tracking import distill_summary

    # Distill surface rows…
    store.record_saving(
        filter_name="test_output", source="cli", command="pytest",
        raw_tokens=1000, distilled_tokens=100,
    )
    store.record_saving(
        filter_name="git_log", source="hook-bash", command="git log",
        raw_tokens=500, distilled_tokens=50,
    )
    # …and an MCP counterfactual row in the same ledger, which distill must skip.
    store.record_saving(
        filter_name="get_context", source="mcp:get_context", command=None,
        raw_tokens=4000, distilled_tokens=400,
    )

    summary = distill_summary(store._conn)
    assert summary["events"] == 2
    assert summary["raw_tokens"] == 1500
    assert summary["saved_tokens"] == 1350
    assert set(summary["per_filter"]) == {"test_output", "git_log"}
    assert "get_context" not in summary["per_filter"]


def test_mcp_savings_summary_counterfactual_precedence(store: OmissionStore) -> None:
    from repowise.core.distill.tracking import mcp_savings_summary

    # Counterfactual ledger rows for two tools (these subsume their own
    # truncation, since delivered is measured post-truncation).
    store.record_saving(
        filter_name="get_symbol", source="mcp:get_symbol", command=None,
        raw_tokens=3000, distilled_tokens=300,
    )
    store.record_saving(
        filter_name="get_symbol", source="mcp:get_symbol", command=None,
        raw_tokens=1000, distilled_tokens=200,
    )
    store.record_saving(
        filter_name="get_context", source="mcp:get_context", command=None,
        raw_tokens=2000, distilled_tokens=500,
    )
    # Truncation drops: get_symbol also has drops (must NOT be added on top —
    # counterfactual wins); get_risk has ONLY drops (its sole signal).
    store.put("x" * 400, source="mcp:get_symbol", original_tokens=900, kept_tokens=0)
    store.put("y" * 400, source="mcp:get_risk", original_tokens=700, kept_tokens=0)
    # A distill omission must never leak into the MCP view.
    store.put("z" * 400, source="cli:logs", original_tokens=999, kept_tokens=0)

    summary = mcp_savings_summary(store._conn)
    by_tool = {row["tool"]: row for row in summary["per_tool"]}

    # get_symbol → counterfactual saved 2700+800=3500, drops ignored.
    assert by_tool["get_symbol"] == {
        "tool": "get_symbol", "events": 2, "tokens": 3500, "kind": "counterfactual",
    }
    # get_context → counterfactual saved 1500.
    assert by_tool["get_context"]["tokens"] == 1500
    assert by_tool["get_context"]["kind"] == "counterfactual"
    # get_risk → truncation only.
    assert by_tool["get_risk"] == {
        "tool": "get_risk", "events": 1, "tokens": 700, "kind": "truncation",
    }
    # queries counts counterfactual events only; tokens is the merged total.
    assert summary["queries"] == 3
    assert summary["tokens"] == 3500 + 1500 + 700
    assert summary["events"] == 2 + 1 + 1
    # Ordered by tokens desc.
    assert [r["tool"] for r in summary["per_tool"]] == ["get_symbol", "get_context", "get_risk"]


def test_mcp_savings_summary_empty(store: OmissionStore) -> None:
    from repowise.core.distill.tracking import mcp_savings_summary

    summary = mcp_savings_summary(store._conn)
    assert summary == {"events": 0, "tokens": 0, "queries": 0, "per_tool": []}


def test_default_store_path_finds_repowise_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    (repo / ".repowise").mkdir()
    path = default_store_path(nested)
    assert path == repo / ".repowise" / "omissions" / "omissions.db"


def test_default_store_path_falls_back_to_home(tmp_path: Path) -> None:
    # No .repowise anywhere up the tree (tmp_path is outside home's subtree
    # on CI runners; if not, the walk stops at home and still falls back).
    path = default_store_path(tmp_path)
    assert path.name == "omissions.db"
    assert ".repowise" in str(path)
