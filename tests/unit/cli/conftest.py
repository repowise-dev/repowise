"""CLI test configuration.

Home isolation (``_isolated_home``) lives in ``tests/unit/conftest.py`` and
covers every unit test, CLI ones included.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _unicode_terminal(monkeypatch):
    """Pin the Unicode glyphs, whatever stdout the test runner itself has.

    Glyphs follow the encoding stdout had when ``repowise.cli`` was imported, so
    a ``-s`` run on a cp1252 pipe would otherwise switch every test to ASCII.
    Tests of the ASCII forms set the encoding themselves.
    """
    from repowise.cli import _stdio

    monkeypatch.setattr(_stdio, "_original_stdout_encoding", "utf-8")
