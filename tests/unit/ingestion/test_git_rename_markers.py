"""Numstat rename-marker expansion, including empty-side directory moves."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.git_indexer.records import _extract_rename_paths


@pytest.mark.parametrize(
    ("stat_path", "want_old", "want_new"),
    [
        # Whole-segment rename with shared suffix.
        ("{old => new}/shared/file.py", "old/shared/file.py", "new/shared/file.py"),
        # Filename rename inside a stable directory.
        ("old_dir/{old_name => new_name}.py", "old_dir/old_name.py", "old_dir/new_name.py"),
        # Directory INSERTED into the path (empty old side) — git emits
        # ``{ => newdir}``; expanding the empty side must not leave a
        # doubled slash.
        (
            "src/settings-ui/Settings.UI/{ => SettingsXAML}/Views/Page.xaml.cs",
            "src/settings-ui/Settings.UI/Views/Page.xaml.cs",
            "src/settings-ui/Settings.UI/SettingsXAML/Views/Page.xaml.cs",
        ),
        # Directory REMOVED from the path (empty new side).
        (
            "src/{legacy => }/Views/Page.xaml.cs",
            "src/legacy/Views/Page.xaml.cs",
            "src/Views/Page.xaml.cs",
        ),
        # Mid-path segment rename.
        ("a/{b => c}/d.py", "a/b/d.py", "a/c/d.py"),
    ],
)
def test_rename_marker_forms(stat_path: str, want_old: str, want_new: str) -> None:
    known: set[str] = set()
    old, new = _extract_rename_paths(stat_path, known)
    assert old == want_old
    assert new == want_new
    assert known == {want_old, want_new}


def test_plain_path_returns_none() -> None:
    known: set[str] = set()
    assert _extract_rename_paths("src/plain/file.py", known) == (None, None)
    assert known == set()
