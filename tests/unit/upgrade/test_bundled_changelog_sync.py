"""Guard: the changelog bundled into the CLI wheel must match docs/CHANGELOG.md.

The CLI ships a copy of ``docs/CHANGELOG.md`` as package data so ``repowise
whats-new`` and the post-upgrade panel work offline from an installed wheel.
This test fails if the two drift, with the exact resync command. Skipped when
the source ``docs/CHANGELOG.md`` is absent (e.g. running against an installed
wheel with no checkout).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.upgrade.release import BUNDLED_CHANGELOG_PATH

_BUNDLED = BUNDLED_CHANGELOG_PATH


def _source_changelog() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "docs" / "CHANGELOG.md"
        if candidate.is_file():
            return candidate
    return None


def test_bundled_changelog_in_sync():
    source = _source_changelog()
    if source is None:
        pytest.skip("no source docs/CHANGELOG.md (installed wheel)")
    assert _BUNDLED.is_file(), "bundled CHANGELOG.md is missing from the cli package data"
    resync = (
        f"copy {source} -> {_BUNDLED}\n"
        f"  (bash: cp '{source}' '{_BUNDLED}'  |  PowerShell: Copy-Item '{source}' '{_BUNDLED}')"
    )
    assert _BUNDLED.read_text(encoding="utf-8") == source.read_text(encoding="utf-8"), (
        f"bundled changelog is stale; resync with:\n  {resync}"
    )
