"""Shared fixtures for distill unit tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from repowise.core.distill.store import OmissionStore


@pytest.fixture(scope="session")
def distill_fixtures_dir(fixtures_dir: Path) -> Path:
    """Path to tests/fixtures/distill/ (captured real command outputs)."""
    path = fixtures_dir / "distill"
    assert path.exists(), f"distill fixtures not found at {path}"
    return path


@pytest.fixture(scope="session")
def load_fixture(distill_fixtures_dir: Path):
    """Loader that tolerates the UTF-8 BOM PowerShell capture leaves behind."""

    def _load(name: str) -> str:
        return (distill_fixtures_dir / name).read_text(encoding="utf-8-sig")

    return _load


@pytest.fixture(scope="session")
def _repowise_stub_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    stub_dir = tmp_path_factory.mktemp("repowise-on-path")
    stub = stub_dir / ("repowise.exe" if sys.platform == "win32" else "repowise")
    stub.write_text("")
    stub.chmod(0o755)
    return stub_dir


@pytest.fixture(autouse=True)
def _repowise_on_path(_repowise_stub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ``repowise`` resolvable on PATH, as a rewrite requires.

    The rewrite hook passes commands through when ``repowise`` is not on
    PATH, so tests that expect a rewrite would otherwise depend on how pytest
    was launched. Tests of the pass-through set their own PATH.
    """
    monkeypatch.setenv("PATH", f"{_repowise_stub_dir}{os.pathsep}{os.environ.get('PATH', '')}")


@pytest.fixture()
def store(tmp_path: Path) -> OmissionStore:
    s = OmissionStore(tmp_path / "omissions" / "omissions.db")
    yield s
    s.close()
