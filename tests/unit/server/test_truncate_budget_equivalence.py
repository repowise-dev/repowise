"""Characterisation test for the _truncate_to_budget O(n^2) -> O(n) optimisation.

The split of ``tool_context.py`` replaced the per-symbol full-response
re-serialisation in stage 2 of ``_truncate_to_budget`` with a precomputed
per-symbol cost + running sum. This test pins that the optimised implementation
produces a **byte-identical** result to the original implementation (embedded
here verbatim as ``_reference_truncate``) across a range of over-budget fixtures.
"""

from __future__ import annotations

import copy
import json

import pytest

from repowise.server.mcp_server.tool_context.truncation import (
    _HEAVY_DOC_FIELDS,
    _query_terms_for,
    _symbol_priority,
    _truncate_to_budget,
)


def _reference_truncate(result: dict, char_budget: int) -> dict:
    """Verbatim copy of the original (pre-optimisation) _truncate_to_budget.

    Stage 2 re-serialises the whole response once per candidate symbol — the
    O(targets x symbols^2) behaviour the optimisation removes. Kept here purely
    as the oracle the optimised implementation must match exactly.
    """
    result.setdefault("truncated", False)
    result.setdefault("dropped_targets", [])
    result.setdefault("dropped_symbols", {})

    targets = result.get("targets") or {}
    if not targets:
        return result

    def _size() -> int:
        return len(json.dumps(result, separators=(",", ":"), default=str))

    if _size() <= char_budget:
        return result

    for _name, tgt in targets.items():
        docs = tgt.get("docs") if isinstance(tgt, dict) else None
        if not isinstance(docs, dict):
            continue
        for field in _HEAVY_DOC_FIELDS:
            if field in docs:
                docs.pop(field, None)
                result["truncated"] = True
        if _size() <= char_budget:
            return result

    def _target_cost(item):
        return len(json.dumps(item[1], default=str))

    for tgt_name, tgt in sorted(targets.items(), key=_target_cost, reverse=True):
        docs = tgt.get("docs") if isinstance(tgt, dict) else None
        if not isinstance(docs, dict):
            continue
        symbols = docs.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            continue
        query_terms = _query_terms_for(tgt_name)
        ordered = sorted(symbols, key=lambda s: _symbol_priority(s, query_terms), reverse=True)
        kept: list[dict] = []
        dropped: list[str] = []
        for sym in ordered:
            docs["symbols"] = [*kept, sym]
            if _size() <= char_budget:
                kept.append(sym)
            else:
                dropped.append(sym.get("name") or "<anonymous>")
        if not kept and ordered:
            head = dict(ordered[0])
            if isinstance(head.get("docstring"), str):
                head["docstring"] = head["docstring"][:200]
            kept = [head]
            dropped = [s.get("name") or "<anonymous>" for s in ordered[1:]]
        docs["symbols"] = kept
        if dropped:
            result["dropped_symbols"][tgt_name] = dropped
            result["truncated"] = True
        if _size() <= char_budget:
            return result

    def _evictable_order():
        items = list(targets.items())
        items.sort(
            key=lambda kv: (
                0 if isinstance(kv[1], dict) and "error" in kv[1] else 1,
                len(json.dumps(kv[1], default=str)),
            ),
            reverse=True,
        )
        return [k for k, _ in items]

    for name in _evictable_order():
        if len(targets) <= 1:
            break
        targets.pop(name, None)
        result["dropped_targets"].append(name)
        result["truncated"] = True
        if _size() <= char_budget:
            break

    return result


def _make_response(n_targets: int, n_symbols: int, body_chars: int, docstring_chars: int) -> dict:
    """Build a synthetic get_context response large enough to exceed a budget."""
    targets: dict[str, dict] = {}
    for ti in range(n_targets):
        path = f"src/pkg{ti}/module_{ti}.py"
        symbols = [
            {
                "name": f"sym_{ti}_{si}",
                "kind": ["class", "function", "method", "helper"][si % 4],
                "signature": f"def sym_{ti}_{si}(arg0, arg1, arg2) -> None",
                "start_line": si * 5 + 1,
                "end_line": si * 5 + 4,
                "docstring": ("d" * docstring_chars),
            }
            for si in range(n_symbols)
        ]
        targets[path] = {
            "target": path,
            "type": "file",
            "docs": {
                "title": f"Module {ti}",
                "summary": "s" * 80,
                "content_md": "m" * body_chars,
                "symbols": symbols,
            },
            "hotspot": ti % 2 == 0,
        }
    return {"targets": targets, "_meta": {"timing_ms": 1.0}}


@pytest.mark.parametrize(
    ("n_targets", "n_symbols", "body_chars", "docstring_chars", "budget"),
    [
        (4, 30, 4000, 120, 8000),
        (6, 60, 5000, 200, 8000),
        (1, 200, 0, 300, 8000),  # single dense target, busts on symbols alone
        (3, 10, 0, 50, 2000),  # tiny budget forces target eviction
        (10, 5, 1500, 40, 6000),
        (2, 1, 0, 50000, 4000),  # single symbol larger than budget
    ],
)
def test_optimised_matches_reference(n_targets, n_symbols, body_chars, docstring_chars, budget):
    fixture = _make_response(n_targets, n_symbols, body_chars, docstring_chars)

    optimised = _truncate_to_budget(copy.deepcopy(fixture), char_budget=budget)
    reference = _reference_truncate(copy.deepcopy(fixture), char_budget=budget)

    # Byte-identical serialisation is the strongest possible equality check.
    assert json.dumps(optimised, sort_keys=True, default=str) == json.dumps(
        reference, sort_keys=True, default=str
    )
    # And the structural bookkeeping fields match exactly.
    assert optimised["truncated"] == reference["truncated"]
    assert optimised["dropped_targets"] == reference["dropped_targets"]
    assert optimised["dropped_symbols"] == reference["dropped_symbols"]
