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
