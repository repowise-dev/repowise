"""Tests for changed-line parsing, including file-level change shape."""
from __future__ import annotations

from repowise.core.analysis.changed_lines import parse_unified_diff


def test_new_file_mode_marker():
    diff = (
        "diff --git a/f.py b/f.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/f.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+a\n"
        "+b\n"
    )
    fd = parse_unified_diff(diff)["f.py"]
    assert fd.new_file is True
    assert fd.mode == "100644"
    assert fd.new_lines == {1, 2}


def test_deleted_file_mode_marker():
    diff = (
        "diff --git a/f.py b/f.py\n"
        "deleted file mode 100644\n"
        "--- a/f.py\n"
        "+++ /dev/null\n"
        "@@ -3,2 +0,0 @@\n"
        "-a\n"
        "-b\n"
    )
    fd = parse_unified_diff(diff)["f.py"]
    assert fd.deleted is True
    assert fd.old_mode == "100644"
    assert fd.old_ranges == [(3, 4)]


def test_mode_change_only():
    diff = (
        "diff --git a/f.py b/f.py\n"
        "old mode 100644\n"
        "new mode 100755\n"
        "--- a/f.py\n"
        "+++ b/f.py\n"
    )
    fd = parse_unified_diff(diff)["f.py"]
    assert fd.old_mode == "100644"
    assert fd.mode == "100755"
    # A chmod has no content hunks, so the coverage view drops it.
    assert fd.new_lines == set()


def test_binary_file_change():
    diff = (
        "diff --git a/logo.png b/logo.png\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/logo.png and b/logo.png differ\n"
    )
    fd = parse_unified_diff(diff)["logo.png"]
    assert fd.binary is True
    assert fd.new_lines == set()
    assert fd.old_ranges == []


def test_unchanged_parse_still_drops_binary_from_coverage_view():
    from repowise.core.analysis.changed_lines import _parse_unified_diff

    diff = (
        "diff --git a/logo.png b/logo.png\n"
        "Binary files a/logo.png and b/logo.png differ\n"
    )
    # No new-side lines -> not part of the coverage intersection.
    assert _parse_unified_diff(diff) == {}
