"""The init/update step behind ``contracts.in_repo``: off by default, never fatal."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands import _index_outcome
from repowise.core.workspace import repo_contracts

_ROUTE = 'from fastapi import FastAPI\napp = FastAPI()\n\n\n@app.get("/ping")\ndef ping():\n    return 1\n'


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    (root / ".repowise").mkdir(parents=True)
    (root / "app.py").write_text(_ROUTE)
    return root


def _turn_on(root: Path) -> None:
    (root / ".repowise" / "config.yaml").write_text("contracts:\n  in_repo: true\n")


def test_off_by_default_it_writes_and_prints_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / ".repowise" / "config.yaml").write_text("provider: openai\n")
    before = _snapshot(repo)

    async def _never(*_a, **_k):
        raise AssertionError("extraction ran with the setting off")

    monkeypatch.setattr(repo_contracts, "run_repo_contract_extraction", _never)
    _index_outcome.refresh_repo_contracts(repo)

    assert _snapshot(repo) == before
    assert capsys.readouterr() == ("", "")


def test_a_broken_config_reads_as_off(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / ".repowise" / "config.yaml").write_text("contracts: [unclosed\n")
    before = _snapshot(repo)

    _index_outcome.refresh_repo_contracts(repo)

    assert _snapshot(repo) == before
    assert capsys.readouterr() == ("", "")


def test_on_it_writes_the_repo_contracts(repo: Path) -> None:
    _turn_on(repo)

    _index_outcome.refresh_repo_contracts(repo)

    assert repo_contracts.repo_contracts_path(repo).is_file()


def test_a_failure_is_named_and_leaves_the_previous_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _turn_on(repo)
    _index_outcome.refresh_repo_contracts(repo)
    path = repo_contracts.repo_contracts_path(repo)
    before = path.read_bytes()
    (repo / "app.py").write_text(_ROUTE + "\n# edited\n")

    async def _boom(*_a, **_k):
        raise RuntimeError("extractor crashed")

    monkeypatch.setattr(repo_contracts, "_extract", _boom)
    _index_outcome.refresh_repo_contracts(repo)  # must not raise

    assert path.read_bytes() == before
    assert "Contracts not refreshed" in capsys.readouterr().out


def test_the_setting_is_registered_off_and_rebuilds_nothing(tmp_path: Path) -> None:
    from repowise.core.repo_config import config_dependency_fingerprints
    from repowise.core.settings import registry

    setting = registry.get("contracts.in_repo")
    assert (setting.default, setting.type) == (False, "bool")

    (tmp_path / ".repowise").mkdir()
    off = config_dependency_fingerprints(tmp_path, config={})
    on = config_dependency_fingerprints(tmp_path, config={"contracts": {"in_repo": True}})
    assert {k for k in on if on[k] != off[k]} == {"state_only"}
