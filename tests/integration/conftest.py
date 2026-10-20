"""Shared setup for the integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def sample_repo_stays_clean(sample_repo_path: Path):
    """Fail the run if a test wrote .repowise/ into the shared sample repo."""
    leftover = sample_repo_path / ".repowise"
    yield
    assert not leftover.exists(), (
        f"{leftover} was created during the run. Use the sample_repo_copy "
        "fixture for tests that write into the sample repo."
    )
