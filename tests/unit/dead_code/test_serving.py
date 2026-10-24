"""Serving stored dead-code findings: filters, tiers, rollups, serialization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.core.analysis.dead_code.risk_factors import (
    RISK_CAP_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
)
from repowise.core.analysis.dead_code.serving import (
    DEFAULT_EXCLUDED_KINDS,
    SUMMARY_SCOPE,
    TIER_FLOORS,
    FindingFilters,
    apply_filters,
    build_summary,
    build_tiers,
    compute_impact,
    cross_repo_cut,
    dead_code_finding_id,
    excluded_kinds,
    is_safe,
    last_meaningful_change,
    merge_summary_counts,
    rollup_by_directory,
    rollup_by_owner,
    safe_only_note,
    serialize_finding,
    serve_findings,
    summary_counts,
)


def _row(path: str, confidence: float, **overrides) -> dict:
    row = {
        "id": f"uuid-{path}-{confidence}",
        "kind": "unused_export",
        "file_path": path,
        "symbol_name": "thing",
        "symbol_kind": "function",
        "start_line": 1,
        "end_line": 10,
        "confidence": confidence,
        "reason": "no references",
        "safe_to_delete": True,
        "lines": 10,
        "last_commit_at": datetime(2026, 1, 2, tzinfo=UTC),
        "commit_count_90d": 0,
        "primary_owner": "Ada",
        "age_days": 400,
    }
    row.update(overrides)
    return row


_ROWS = [
    _row("src/a/one.py", 0.9),
    _row("src/a/two.py", 0.95, lines=3),
    _row("src/b/three.py", 0.5, primary_owner="Grace"),
    _row("lib/four.py", 0.2, primary_owner=None, kind="unreachable_file"),
    _row("src/b/five.py", 0.9, lines=40),
]


def _filters(**overrides) -> FindingFilters:
    base = {
        "kind": None,
        "safe_only": False,
        "min_confidence": 0.0,
        "directory": None,
        "owner": None,
        "excluded_kinds": set(),
    }
    base.update(overrides)
    return FindingFilters(**base)


def test_tier_floors_are_the_engine_thresholds() -> None:
    assert TIER_FLOORS == {
        "high": SAFE_CONFIDENCE_THRESHOLD,
        "medium": RISK_CAP_CONFIDENCE,
        "low": 0.0,
    }


def test_excluded_kinds_follow_the_scope_flags() -> None:
    assert {"unused_internal"} == DEFAULT_EXCLUDED_KINDS
    assert excluded_kinds(
        no_unreachable=True,
        no_unused_exports=True,
        include_internals=True,
        include_zombie_packages=False,
    ) == {"unreachable_file", "unused_export", "zombie_package"}


def test_apply_filters_reads_rows_and_mappings_alike() -> None:
    namespaces = [SimpleNamespace(**r) for r in _ROWS]
    for rows in (_ROWS, namespaces):
        assert len(apply_filters(rows, _filters(min_confidence=0.4))) == 4
        assert len(apply_filters(rows, _filters(kind="unreachable_file"))) == 1
        assert len(apply_filters(rows, _filters(excluded_kinds={"unused_export"}))) == 1
        assert len(apply_filters(rows, _filters(directory="src/a/"))) == 2
        assert len(apply_filters(rows, _filters(owner="grace"))) == 1


def test_applied_echoes_only_what_differs_from_the_defaults() -> None:
    defaults = _filters(min_confidence=RISK_CAP_CONFIDENCE, excluded_kinds=DEFAULT_EXCLUDED_KINDS)
    assert defaults.applied() == {}
    summary: dict = {}
    defaults.summarize(summary)
    assert summary == {"scope": SUMMARY_SCOPE}

    narrowed = _filters(kind="unused_export", safe_only=True, directory="src", owner="Ada")
    assert narrowed.applied() == {
        "kind": "unused_export",
        "safe_only": True,
        "min_confidence": 0.0,
        "directory": "src",
        "owner": "Ada",
    }


def test_build_summary_counts_every_shown_finding() -> None:
    summary = build_summary(summary_counts(_ROWS), 2, _filters(), {})
    assert summary["total_findings"] == 5
    assert summary["filtered_findings"] == 2
    assert summary["by_kind"] == {"unused_export": 4, "unreachable_file": 1}
    assert list(summary)[:5] == [
        "total_findings",
        "filtered_findings",
        "deletable_lines",
        "safe_to_delete_count",
        "by_kind",
    ]
    assert "withheld_types" not in summary


def test_safe_only_note_explains_an_empty_safe_only_view() -> None:
    unproven = [_row("src/a/one.py", 0.6, safe_to_delete=False)]
    summary = build_summary(summary_counts(unproven), 0, _filters(safe_only=True), {})
    assert summary["safe_only_note"] == safe_only_note(1, 0)
    assert "1 finding(s), none deletion-ready" in summary["safe_only_note"]

    # Safe rows exist, nothing to explain; nor when there are no findings.
    safe = build_summary(summary_counts(_ROWS), 3, _filters(safe_only=True), {})
    assert "safe_only_note" not in safe
    empty = build_summary(summary_counts([]), 0, _filters(safe_only=True), {})
    assert "safe_only_note" not in empty
    # Without safe_only the summary never carries it.
    assert "safe_only_note" not in build_summary(summary_counts(unproven), 1, _filters(), {})


def test_a_withheld_type_names_where_it_was_measured() -> None:
    # The reason is served for any repository, so it states its corpus rather
    # than reading as a fact about the code at hand (here Kotlin).
    filters = _filters(withheld_kinds=frozenset({"unused_internal"}))
    _shown, withheld = filters.split_withheld([_row("src/App.kt", 0.9, kind="unused_internal")])
    summary = build_summary(summary_counts([]), 0, filters, withheld)
    reason = summary["withheld_types"]["unused_internal"]["reason"]
    assert reason.startswith("Measured on TS/Python code:")


def test_serialization_is_the_same_for_rows_and_mappings() -> None:
    row = _ROWS[0]
    as_dict = serialize_finding(row, repository="r")
    assert serialize_finding(SimpleNamespace(**row), repository="r") == as_dict
    assert as_dict["id"] == dead_code_finding_id(row, "r")
    assert as_dict["last_commit_at"] == "2026-01-02T00:00:00+00:00"


def test_serialization_adds_the_last_meaningful_change() -> None:
    gm = SimpleNamespace(significant_commits_json=json.dumps([{"date": "2026-03-01"}]))
    out = serialize_finding(_ROWS[0], {"src/a/one.py": gm})
    assert out["last_meaningful_change"] == "2026-03-01"
    assert last_meaningful_change(SimpleNamespace(significant_commits_json="not json")) is None
    assert last_meaningful_change(None) is None


def test_build_tiers_bands_sorts_and_reports_overflow() -> None:
    serialized = [serialize_finding(r) for r in _ROWS]
    dropped: list[tuple[str, int]] = []
    tiers = build_tiers(serialized, 2, None, on_overflow=lambda n, b: dropped.append((n, len(b))))

    assert list(tiers) == ["high", "medium", "low"]
    high = tiers["high"]
    assert [f["file_path"] for f in high["findings"]] == ["src/a/two.py", "src/b/five.py"]
    assert (high["count"], high["lines"], high["truncated"]) == (3, 53, True)
    assert dropped == [("high", 1)]
    assert build_tiers(serialized, 2, "low")["low"]["count"] == 1
    assert list(build_tiers(serialized, 2, "medium")) == ["medium"]


def test_single_repo_and_workspace_tiering_have_the_same_shape() -> None:
    # The single-repo path serializes ORM-like rows; the workspace path merges
    # repos, tags each finding with its repo and arrives in a different order.
    single = build_tiers([serialize_finding(SimpleNamespace(**r)) for r in _ROWS], 2, None)
    merged = []
    for r in reversed(_ROWS):
        item = serialize_finding(r)
        item["repo"] = "default"
        merged.append(item)
    workspace = build_tiers(merged, 2, None)

    for tier in workspace.values():
        for f in tier["findings"]:
            f.pop("repo")
    # Equal-score ties keep their input order; this fixture has none.
    assert workspace == single


def test_rollups_group_and_order_by_lines() -> None:
    assert rollup_by_directory(_ROWS)[0] == {
        "directory": "src/b",
        "count": 2,
        "lines": 50,
        "safe_count": 1,
    }
    owners = {o["owner"]: o["count"] for o in rollup_by_owner(_ROWS)}
    assert owners == {"Ada": 3, "Grace": 1, "unowned": 1}


def test_compute_impact_counts_safe_lines_shown() -> None:
    tiers = build_tiers([serialize_finding(r) for r in _ROWS], 25, None)
    impact = compute_impact(tiers)
    assert impact["total_lines_reclaimable"] == 73
    assert impact["safe_lines_reclaimable"] == 53
    assert compute_impact({})["recommendation"] == "No dead code found matching your filters."


class _Lookup:
    def has_cross_repo_consumers(self, repo_alias: str, file_path: str) -> list[dict]:
        return [{"repo": "web"}] if file_path == "src/a/one.py" else []

    def get_repos_depending_on(self, repo_alias: str) -> list[str]:
        return ["mobile"]

    def get_package_consumers(self, repo_alias: str) -> list[dict]:
        return [{"source_repo": "mobile", "target_manifest": "src/b/package.json"}]

    def get_contract_links_as_provider(self, repo_alias: str, file_path: str) -> list[dict]:
        return [{"consumer_repo": "web"}] if file_path == "src/a/two.py" else []


def _served(rows: list, lookup=None, alias: str | None = "api") -> list[dict]:
    return [serialize_finding(f) for f in serve_findings(rows, lookup or _Lookup(), alias)]


def test_only_a_contract_link_lowers_confidence() -> None:
    by_path = {f["file_path"]: f for f in _served(_ROWS)}
    # Contract provider: cut to 30%, the consumer named.
    assert by_path["src/a/two.py"]["confidence"] == 0.28
    assert "consumed by: web." in by_path["src/a/two.py"]["cross_repo_note"]
    # Co-change partners and package dependents keep the confidence.
    assert by_path["src/a/one.py"]["confidence"] == 0.9
    assert "changes together with 1 file(s) in web" in by_path["src/a/one.py"]["cross_repo_note"]
    assert by_path["src/b/five.py"]["confidence"] == 0.9
    assert "mobile may consume this export." in by_path["src/b/five.py"]["cross_repo_note"]
    # A finding another repo may consume is never safe to delete.
    assert not any(f["safe_to_delete"] for f in by_path.values())


def test_package_dependents_are_named_only_where_their_link_reaches() -> None:
    # Outside every link: nobody is named, yet safety is withdrawn.
    lone = _served([_row("src/c/six.py", 0.9)])
    assert "mobile" not in lone[0]["cross_repo_note"]
    assert lone[0]["confidence"] == 0.9
    assert not lone[0]["safe_to_delete"]


class _ContractOnlyLookup(_Lookup):
    """A provider repo nothing depends on as a package, e.g. an HTTP backend."""

    def get_repos_depending_on(self, repo_alias: str) -> list[str]:
        return []

    def get_package_consumers(self, repo_alias: str) -> list[dict]:
        return []


def test_a_contract_link_cuts_every_kind_without_package_dependents() -> None:
    rows = [
        _row("src/a/two.py", 0.9, kind="unreachable_file"),
        _row("src/a/two.py", 0.95),
        _row("src/c/six.py", 0.9, kind="unreachable_file"),
    ]
    file_row, export_row, untouched = _served(rows, _ContractOnlyLookup())
    assert file_row["confidence"] == 0.27
    assert file_row["cross_repo_note"].startswith("This file may be consumed by: web.")
    assert export_row["cross_repo_note"].startswith("This export may be consumed by: web.")
    assert not file_row["safe_to_delete"] and not export_row["safe_to_delete"]
    assert untouched["confidence"] == 0.9 and "cross_repo_note" not in untouched


def test_a_same_repo_contract_link_is_not_a_cross_repo_consumer() -> None:
    class _SelfLinked(_ContractOnlyLookup):
        def get_contract_links_as_provider(self, repo_alias: str, file_path: str) -> list[dict]:
            return [{"consumer_repo": repo_alias}]

    assert cross_repo_cut(_SelfLinked(), "api", "src/a/two.py", "unreachable_file") is None


class _UnscopedLookup(_Lookup):
    def get_package_consumers(self, repo_alias: str) -> list[dict]:
        return [{"source_repo": "mobile", "target_manifest": ""}]


def test_a_package_link_with_no_manifest_names_its_dependent() -> None:
    # Unknown package scope is not evidence the file sits outside it.
    lone = _served([_row("src/c/six.py", 0.9)], _UnscopedLookup())
    assert "mobile may consume this export." in lone[0]["cross_repo_note"]


def test_cut_findings_are_tiered_by_their_lowered_confidence() -> None:
    tiers = build_tiers(_served(_ROWS), 25, None)
    for name, block in tiers.items():
        for f in block["findings"]:
            assert f["confidence"] >= TIER_FLOORS[name]
    assert "src/a/two.py" in {f["file_path"] for f in tiers["low"]["findings"]}


def test_the_floor_and_safe_only_read_the_cut_confidence() -> None:
    # 0.95 * 0.3 = 0.28: below the default floor once cut, so not listed.
    kept = serve_findings(_ROWS, _Lookup(), "api", _filters(min_confidence=RISK_CAP_CONFIDENCE))
    assert all(f.confidence >= RISK_CAP_CONFIDENCE for f in kept)
    assert "src/a/two.py" not in {f.file_path for f in kept}
    assert serve_findings(_ROWS, _Lookup(), "api", _filters(safe_only=True)) == []


def test_serve_findings_without_data_or_alias_changes_nothing() -> None:
    plain = [serialize_finding(r) for r in _ROWS]
    assert _served(_ROWS, lookup=None, alias=None) == plain
    assert [serialize_finding(f) for f in serve_findings(_ROWS, None, "api")] == plain


def test_recommendation_never_points_at_an_empty_tier() -> None:
    def advice(rows: list[dict], tier: str | None = None) -> str:
        tiers = build_tiers([serialize_finding(r) for r in rows], 25, tier)
        return compute_impact(tiers)["recommendation"]

    assert advice(_ROWS).startswith("Start with the 'high' tier")
    assert advice(_ROWS, "medium").startswith("Review the 'medium' tier")
    assert advice([_row("src/x.py", 0.5)]).startswith("Review the 'medium' tier")
    assert advice([_row("src/x.py", 0.1)]).startswith("Only 'low' tier findings")


def test_merged_per_repo_counts_equal_counting_everything_at_once() -> None:
    parts = [summary_counts(_ROWS[:2]), summary_counts([]), summary_counts(_ROWS[2:])]
    merged = merge_summary_counts(parts)
    whole = summary_counts(_ROWS)
    assert merged == whole
    assert list(merged["by_kind"]) == list(whole["by_kind"])


def test_summary_safe_counts_drop_what_the_cross_repo_cut_withdraws() -> None:
    plain = summary_counts(_ROWS)
    served = serve_findings(_ROWS, _Lookup(), "api")
    cut = summary_counts(served)
    assert cut["safe_to_delete_count"] == sum(f["safe_to_delete"] for f in _served(_ROWS))
    assert cut["safe_to_delete_count"] <= plain["safe_to_delete_count"]
    assert cut["total_findings"] == plain["total_findings"]


def test_the_cut_never_makes_a_finding_safe() -> None:
    rows = [*_ROWS, _row("src/a/one.py", 0.9, safe_to_delete=False)]
    for served in serve_findings(rows, _Lookup(), "api"):
        assert served.confidence <= served.row["confidence"]
        assert not is_safe(served) or is_safe(served.row)
