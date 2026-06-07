"""The git-metadata upsert must not wipe repo-wide-walk signals.

``co_change_partners_json`` and ``change_entropy`` are produced by the
repo-wide co-change walk. A pass that did not run the walk (ESSENTIAL tier,
legacy incremental updates) reports the empty default for both — blindly
overwriting blanked the init-computed values for exactly the files that
change most.
"""

from __future__ import annotations

from repowise.core.persistence.crud.git import _update_git_metadata
from repowise.core.persistence.models import GitMetadata


def _existing(**kwargs) -> GitMetadata:
    return GitMetadata(file_path="src/a.py", **kwargs)


class TestWalkFieldPreservation:
    def test_empty_partners_do_not_overwrite(self):
        existing = _existing(co_change_partners_json='[{"file_path": "src/b.py"}]')
        _update_git_metadata(existing, {"co_change_partners_json": "[]"})
        assert existing.co_change_partners_json == '[{"file_path": "src/b.py"}]'

    def test_fresh_partners_do_overwrite(self):
        existing = _existing(co_change_partners_json='[{"file_path": "src/b.py"}]')
        _update_git_metadata(existing, {"co_change_partners_json": '[{"file_path": "src/c.py"}]'})
        assert existing.co_change_partners_json == '[{"file_path": "src/c.py"}]'

    def test_empty_over_empty_is_fine(self):
        existing = _existing(co_change_partners_json="[]")
        _update_git_metadata(existing, {"co_change_partners_json": "[]"})
        assert existing.co_change_partners_json == "[]"

    def test_zero_entropy_does_not_overwrite(self):
        existing = _existing(change_entropy=1.37)
        _update_git_metadata(existing, {"change_entropy": 0.0})
        assert existing.change_entropy == 1.37

    def test_real_entropy_does_overwrite(self):
        existing = _existing(change_entropy=1.37)
        _update_git_metadata(existing, {"change_entropy": 2.5})
        assert existing.change_entropy == 2.5

    def test_other_fields_still_overwrite_normally(self):
        existing = _existing(commit_count_total=5)
        _update_git_metadata(existing, {"commit_count_total": 0})
        assert existing.commit_count_total == 0
