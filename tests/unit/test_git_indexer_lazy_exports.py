"""Cold imports stay light while the git indexer's public exports still work."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


def _run_cold(code: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "module",
    [
        "repowise.core.ingestion.git_indexer",
        "repowise.core.ingestion.git_indexer.function_blame",
        "repowise.core.analysis.health.biomarkers.base",
    ],
)
def test_helper_import_does_not_load_gitpython_or_indexer(module: str) -> None:
    _run_cold(f"""
        import importlib
        import sys

        importlib.import_module({module!r})
        assert "git" not in sys.modules
        assert "repowise.core.ingestion.git_indexer.indexer" not in sys.modules
        assert "repowise.core.ingestion.git_indexer.backfill" not in sys.modules
    """)


def test_public_exports_are_discoverable_and_resolve_to_their_defining_modules() -> None:
    _run_cold("""
        import importlib
        import repowise.core.ingestion.git_indexer as package

        assert len(package.__all__) == 41
        assert set(package.__all__) == set(package._EXPORTS)
        assert set(package.__all__) <= set(dir(package))
        for name in package.__all__:
            value = getattr(package, name)
            assert getattr(package, name) is value
            if hasattr(value, "__module__"):
                defining_module = importlib.import_module(value.__module__)
                assert getattr(defining_module, name) is value
        namespace = {}
        exec("from repowise.core.ingestion.git_indexer import *", namespace)
        assert set(package.__all__) <= namespace.keys()
    """)


def test_gitpython_noise_patch_is_installed_before_indexer_use() -> None:
    _run_cold("""
        from git.cmd import _CatFileContentStream
        original = _CatFileContentStream.__del__
        from repowise.core.ingestion.git_indexer import GitIndexer
        from repowise.core.ingestion.git_indexer import indexer

        assert GitIndexer.__name__ == "GitIndexer"
        assert indexer._orig_del is original
        assert _CatFileContentStream.__del__ is indexer._quiet_del
        def closed_stream(self):
            raise ValueError("read of closed file")
        indexer._orig_del = closed_stream
        _CatFileContentStream.__del__(object())
    """)
