"""Unit tests for c4_builder.signals — per-box hotspot/dead aggregation."""

from __future__ import annotations

from repowise.server.services.c4_builder.signals import count_box_signals


def test_count_box_signals_buckets_per_box():
    file_to_box = {
        "a/x.py": "pkg:a",
        "a/y.py": "pkg:a",
        "b/z.py": "pkg:b",
        "b/q.py": "pkg:b",
    }
    hotspots = ["a/x.py", "b/z.py", "b/q.py"]
    dead = ["a/y.py"]
    counts = count_box_signals(file_to_box, hotspots, dead)
    assert counts == {"pkg:a": (1, 1), "pkg:b": (2, 0)}


def test_count_box_signals_includes_zero_boxes():
    file_to_box = {"a/x.py": "pkg:a"}
    counts = count_box_signals(file_to_box, [], [])
    assert counts == {"pkg:a": (0, 0)}


def test_count_box_signals_ignores_unmapped_paths():
    # A flagged path with no box mapping must not crash or count anywhere.
    counts = count_box_signals({"a/x.py": "pkg:a"}, ["unmapped/file.py"], [])
    assert counts == {"pkg:a": (0, 0)}
