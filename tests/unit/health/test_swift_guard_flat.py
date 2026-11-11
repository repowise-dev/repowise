"""Swift ``guard ... else { return }`` is a decision but opens no nesting level."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

_SRC = """
func load(_ data: Data?, _ key: String?) -> Int {
    guard let data = data else { return 0 }
    guard let key = key, !key.isEmpty else { return 1 }
    if data.count > 0 {
        return 2
    }
    return 3
}

func nested(_ a: Int?) -> Int {
    if let a = a {
        guard a > 0 else { return 0 }
        return a
    }
    return 1
}
"""


def _fns() -> dict:
    fcx = walk_file("/tmp/sample.swift", "swift", _SRC.encode())
    if not fcx.functions:
        pytest.skip("swift grammar unavailable")
    return {fc.name: fc for fc in fcx.functions}


def test_swift_guard_counts_but_does_not_nest() -> None:
    fn = _fns()["load"]
    assert fn.ccn == 4
    assert fn.max_nesting == 1


def test_swift_guard_inside_a_branch_adds_no_level() -> None:
    assert _fns()["nested"].max_nesting == 1
