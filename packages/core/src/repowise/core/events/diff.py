"""Typed occurrences between two consecutive same-stamp component snapshots.

Pure: two ``{component: facts}`` maps in, ``(kind, component, subject,
evidence)`` tuples out. A component diffs only when it exists in both runs
with the same definition (paths, root, source), so a re-drawn component
never reads as its facts moving. Every rule prefers a miss over a false
hit: it needs a measured basis on both sides and a size floor.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

#: Criticality levels a "critical component" rule applies to.
CRITICAL_LEVELS = frozenset({"high", "critical"})

#: Below this many owned files ownership figures are anecdotes.
MIN_OWNED_FILES = 5
#: Main-owner sets this dissimilar (Jaccard) count as a shift.
OWNERSHIP_SHIFT_JACCARD = 0.5
#: Dead code must grow by both of these to count.
DEAD_CODE_MIN_FINDINGS = 5
DEAD_CODE_MIN_RATIO = 0.25
#: Test reach must fall by this share, over at least this many production files.
TEST_REACH_MIN_DROP = 0.10
TEST_REACH_MIN_FILES = 5
#: Measured line coverage must fall by this many points.
LINE_COVERAGE_MIN_DROP = 5.0

Occurrence = tuple[str, str, str, dict[str, Any]]


def diff_snapshots(
    previous: Mapping[str, Mapping[str, Any]], current: Mapping[str, Mapping[str, Any]]
) -> list[Occurrence]:
    """Every occurrence between *previous* and *current*, in a stable order."""
    out: list[Occurrence] = []
    for name in sorted(current):
        before, after = previous.get(name), current[name]
        if before is None or before.get("definition") != after.get("definition"):
            continue
        for rule in _RULES:
            out.extend((kind, name, subject, ev) for kind, subject, ev in rule(before, after))
    return out


def contract_occurrences(
    component: str, before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[Occurrence]:
    """The contract rule alone, for one component's facts at two runs."""
    return [(kind, component, subject, ev) for kind, subject, ev in _contracts(before, after)]


def _get(facts: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = facts.get(key)
    return value if isinstance(value, dict) else {}


def _same_key(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """Owner digests compare only when both runs used the same digest key."""
    return bool(before.get("key_id")) and before.get("key_id") == after.get("key_id")


def _ownership(before: Mapping[str, Any], after: Mapping[str, Any]) -> Iterator[tuple]:
    if not _same_key(before, after):
        return
    was, now = before.get("declared_owner_key"), after.get("declared_owner_key")
    if was and now and was != now:
        ev: dict[str, Any] = {"basis": "declared"}
        # Group refs are named; any other owner is reported as changed, unnamed.
        if before.get("declared_owner"):
            ev["previous_owner"] = before["declared_owner"]
        if after.get("declared_owner"):
            ev["owner"] = after["declared_owner"]
        yield "ownership_shift", "declared", ev
        return
    a, b = _get(before, "ownership"), _get(after, "ownership")
    if min(a.get("owned_files", 0), b.get("owned_files", 0)) < MIN_OWNED_FILES:
        return
    old, new = set(a.get("main_owners") or ()), set(b.get("main_owners") or ())
    # New snapshots identify the whole tie at the cutoff. Remove those
    # identities from both comparisons, including a legacy snapshot without
    # the tie fact; its alphabetically selected member is not a real shift.
    tied = set(a.get("margin_tied_owners") or ()) | set(b.get("margin_tied_owners") or ())
    stable_old, stable_new = old - tied, new - tied
    if stable_old and stable_new and len(stable_old & stable_new) / len(stable_old | stable_new) < OWNERSHIP_SHIFT_JACCARD:
        yield "ownership_shift", "observed", {
            "basis": "observed",
            "main_owners_before": len(old),
            "main_owners_after": len(new),
            "main_owners_kept": len(old & new),
            "owned_files": b["owned_files"],
        }


def _owner_inactive(before: Mapping[str, Any], after: Mapping[str, Any]) -> Iterator[tuple]:
    a, b = _get(before, "ownership"), _get(after, "ownership")
    if not _same_key(before, after) or not b.get("main_owners") or b.get("recent_capped"):
        return
    if b.get("owned_files", 0) < MIN_OWNED_FILES or a.get("main_owners") != b.get("main_owners"):
        return
    # Others still change it: a quiet component is stable, not abandoned.
    if b.get("recent_commits", 0) == 0:
        return
    if a.get("active_main_owners", 0) > 0 and b.get("active_main_owners") == 0:
        yield "owner_inactive", "", {
            "basis": "observed",
            "main_owners": len(b.get("main_owners") or ()),
            "window_days": b.get("window_days"),
            "recent_commits": b.get("recent_commits"),
        }


def _bus_factor(before: Mapping[str, Any], after: Mapping[str, Any]) -> Iterator[tuple]:
    if after.get("criticality") not in CRITICAL_LEVELS:
        return
    a, b = _get(before, "bus_factor"), _get(after, "bus_factor")
    if a.get("basis") != "measured" or b.get("basis") != "measured":
        return
    if b["value"] < a["value"]:
        yield "bus_factor_drop", "", {
            "basis": "measured",
            "previous": a["value"],
            "value": b["value"],
            "owned_files": b.get("owned_files"),
        }


def _hotspots(before: Mapping[str, Any], after: Mapping[str, Any]) -> Iterator[tuple]:
    old = set(before.get("hotspots") or ())
    new = sorted(set(after.get("hotspots") or ()) - old)
    if new:
        yield "new_hotspot", ",".join(new), {
            "basis": "measured",
            "files": new,
            "hotspots_before": len(old),
            "hotspots_after": len(after.get("hotspots") or ()),
        }


def _contracts(before: Mapping[str, Any], after: Mapping[str, Any]) -> Iterator[tuple]:
    a, b = _get(before, "contracts"), _get(after, "contracts")
    if a.get("basis") != "measured" or b.get("basis") != "measured":
        return
    old, new = a.get("provided") or {}, b.get("provided") or {}
    for cid in sorted(set(new) - set(old)):
        yield "contract_added", cid, {"basis": "measured", "contract_id": cid}
    for cid in sorted(set(old) - set(new)):
        if old[cid] > 0:  # gone with nobody reading it is a removal, not a break
            yield "contract_broken", cid, {
                "basis": "measured",
                "contract_id": cid,
                "consumers": old[cid],
            }


def _decisions(before: Mapping[str, Any], after: Mapping[str, Any]) -> Iterator[tuple]:
    old = set(_get(before, "decisions").get("stale") or ())
    for did in sorted(set(_get(after, "decisions").get("stale") or ()) - old):
        yield "decision_stale", did, {"basis": "measured", "decision_id": did}


def _dead_code(before: Mapping[str, Any], after: Mapping[str, Any]) -> Iterator[tuple]:
    a, b = _get(before, "dead_code"), _get(after, "dead_code")
    if "findings" not in a or "findings" not in b:
        return
    grew = b["findings"] - a["findings"]
    if grew >= DEAD_CODE_MIN_FINDINGS and grew >= DEAD_CODE_MIN_RATIO * max(a["findings"], 1):
        yield "dead_code_growth", "", {
            "basis": b.get("basis"),
            "previous_findings": a["findings"],
            "findings": b["findings"],
            "previous_lines": a.get("lines"),
            "lines": b.get("lines"),
        }


def _test_reach(before: Mapping[str, Any], after: Mapping[str, Any]) -> Iterator[tuple]:
    a, b = _get(before, "test_reach"), _get(after, "test_reach")
    if "share" not in a or "share" not in b:
        return
    if min(a["production_files"], b["production_files"]) < TEST_REACH_MIN_FILES:
        return
    ev: dict[str, Any] = {}
    if round(a["share"] - b["share"], 3) >= TEST_REACH_MIN_DROP:
        ev.update(
            basis=b.get("basis"),
            previous_share=a["share"],
            share=b["share"],
            production_files=b["production_files"],
        )
    la, lb = a.get("line_coverage") or {}, b.get("line_coverage") or {}
    if "pct" in la and "pct" in lb and round(la["pct"] - lb["pct"], 3) >= LINE_COVERAGE_MIN_DROP:
        ev["line_coverage"] = {"basis": "measured", "previous_pct": la["pct"], "pct": lb["pct"]}
    if ev:
        yield "test_reach_drop", "", ev


_RULES = (
    _ownership,
    _owner_inactive,
    _bus_factor,
    _hotspots,
    _contracts,
    _decisions,
    _dead_code,
    _test_reach,
)
