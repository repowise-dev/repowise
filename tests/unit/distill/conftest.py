"""Shared fixtures for distill unit tests."""

from __future__ import annotations

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


@pytest.fixture()
def store(tmp_path: Path) -> OmissionStore:
    s = OmissionStore(tmp_path / "omissions" / "omissions.db")
    yield s
    s.close()
