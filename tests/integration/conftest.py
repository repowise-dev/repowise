"""Keep integration tests from modifying the shared source fixtures."""

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _sample_repo_stays_clean(sample_repo_path: Path) -> Iterator[None]:
    yield
    state_dir = sample_repo_path / ".repowise"
    assert not state_dir.exists(), (
        f"Integration tests left state in the shared sample fixture: {state_dir}. "
        "Use sample_repo_copy for tests that write repository state."
    )
