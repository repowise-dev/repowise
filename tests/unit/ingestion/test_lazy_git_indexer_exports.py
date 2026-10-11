"""Guard tests for the lazy (PEP 562) ``git_indexer`` exports.

The health biomarkers import one small helper (``function_blame.BlameIndex``)
and must not pay for the whole indexer stack via the package ``__init__``.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import repowise.core.ingestion.git_indexer as git_indexer


def test_all_exports_resolve():
    for name in git_indexer.__all__:
        assert getattr(git_indexer, name) is not None, name


def test_dir_includes_lazy_exports():
    listing = dir(git_indexer)
    assert "GitIndexer" in listing
    assert "index_file" in listing
    assert "git_history_stale" in listing


def test_unknown_attribute_raises():
    with pytest.raises(AttributeError, match="does_not_exist"):
        _ = git_indexer.does_not_exist


def test_biomarkers_import_does_not_load_indexer_stack():
    """Importing the health biomarkers must not pull the indexer submodules
    through the package ``__init__`` — only ``function_blame`` (and its
    ``_constants`` dependency) may load. ``fix_shape`` is excluded: the
    ``untested_hotspot`` biomarker imports it directly, which is genuine use,
    not ``__init__`` eager loading."""
    heavy = (
        "indexer",
        "records",
        "enrich",
        "co_change",
        "prior_defects",
        "agent_provenance",
        "backfill",
        "file_history",
        "fix_events",
        "identity",
        "tiers",
    )
    code = (
        "import sys; import repowise.core.analysis.health.biomarkers.base; "
        "heavy = [m for m in sys.modules "
        "if m.startswith('repowise.core.ingestion.git_indexer.') "
        f"and m.split('.')[-1] in {heavy!r}]; "
        "sys.exit(1 if heavy else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_lazy_attribute_access_imports_on_demand():
    code = (
        "from repowise.core.ingestion.git_indexer import GitIndexer; "
        "import sys; "
        "assert 'repowise.core.ingestion.git_indexer.indexer' in sys.modules; "
        "assert GitIndexer.__name__ == 'GitIndexer'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
