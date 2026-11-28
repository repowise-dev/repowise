"""CLI test configuration.

Home isolation (``_isolated_home``) lives in ``tests/unit/conftest.py`` and
covers every unit test. CLI tests also pin ``Path.home`` to that directory, so
a command that writes user-level config cannot reach the real one even through
a variable the shared fixture does not move.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _pinned_home(_isolated_home, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: _isolated_home)


@pytest.fixture(autouse=True)
def _unicode_terminal(monkeypatch):
    """Pin the Unicode glyphs, whatever stdout the test runner itself has.

    Glyphs follow the encoding stdout had when ``repowise.cli`` was imported, so
    a ``-s`` run on a cp1252 pipe would otherwise switch every test to ASCII.
    Tests of the ASCII forms set the encoding themselves.
    """
    from repowise.cli import _stdio

    monkeypatch.setattr(_stdio, "_original_stdout_encoding", "utf-8")
