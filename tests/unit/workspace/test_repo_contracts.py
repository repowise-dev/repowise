"""Single-repo contracts: ``.repowise/contracts.json`` behind ``contracts.in_repo``."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from repowise.core.workspace import repo_contracts
from repowise.core.workspace.repo_contracts import (
    repo_contracts_path,
    run_repo_contract_extraction,
)

_ROUTE = """from fastapi import FastAPI

app = FastAPI()


@app.get("/users/{user_id}")
def get_user(user_id: int):
    return {"id": user_id}
"""

_CALL = """export async function loadUser(id: string) {
  const res = await fetch(`/users/${id}`);
  return res.json();
}
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@e.x", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _commit(repo: Path, message: str = "c") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


@pytest.fixture
def mono(tmp_path: Path) -> Path:
    """One repo, two services: a FastAPI api and a web client that calls it."""
    repo = tmp_path / "mono"
    (repo / "services" / "api").mkdir(parents=True)
    (repo / "web" / "src").mkdir(parents=True)
    (repo / "services" / "api" / "pyproject.toml").write_text('[project]\nname = "api"\n')
    (repo / "services" / "api" / "app.py").write_text(_ROUTE)
    (repo / "web" / "package.json").write_text('{"name": "web"}\n')
    (repo / "web" / "src" / "api.ts").write_text(_CALL)
    (repo / "README.md").write_text("# mono\n")
    _git(repo, "init", "-q")
    _commit(repo, "init")
    return repo


def _run(repo: Path) -> repo_contracts.RepoContractsResult:
    return asyncio.run(run_repo_contract_extraction(repo))


def _store(repo: Path) -> dict:
    return json.loads(repo_contracts_path(repo).read_text(encoding="utf-8"))


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_a_caller_links_to_the_route_another_service_serves(mono: Path) -> None:
    result = _run(mono)

    assert result.extracted
    store = _store(mono)
    assert {(c["role"], c["file_path"], c["service"]) for c in store["contracts"]} == {
        ("provider", "services/api/app.py", "services/api"),
        ("consumer", "web/src/api.ts", "web"),
    }
    [link] = store["contract_links"]
    assert (link["consumer_file"], link["provider_file"]) == (
        "web/src/api.ts",
        "services/api/app.py",
    )
    assert store["repo_provenance"]["mono"]["head"] == _head(mono)


def test_a_second_run_with_nothing_changed_reuses_the_file_untouched(mono: Path) -> None:
    _run(mono)
    path = repo_contracts_path(mono)
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    result = _run(mono)

    assert not result.extracted
    assert (result.contracts, result.links) == (2, 1)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_a_commit_that_touches_no_contract_file_reuses_and_restamps_head(
    mono: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(mono)
    rows = _store(mono)["contracts"]
    (mono / "README.md").write_text("# mono, again\n")
    _commit(mono, "docs")

    async def _no_extract(*_a, **_k):
        raise AssertionError("re-extracted for a README commit")

    monkeypatch.setattr(repo_contracts, "_extract", _no_extract)
    result = _run(mono)

    assert not result.extracted
    store = _store(mono)
    assert store["contracts"] == rows
    assert store["repo_provenance"]["mono"]["head"] == _head(mono)


def test_a_commit_that_touches_a_source_file_re_extracts(mono: Path) -> None:
    _run(mono)
    (mono / "services" / "api" / "app.py").write_text(
        _ROUTE + '\n\n@app.post("/orders")\ndef create_order():\n    return {}\n'
    )
    _commit(mono, "orders")

    result = _run(mono)

    assert result.extracted
    ids = {c["contract_id"] for c in _store(mono)["contracts"]}
    assert any("/orders" in i for i in ids)


def test_an_uncommitted_edit_is_read_then_reused_then_reverted(mono: Path) -> None:
    _run(mono)
    app = mono / "services" / "api" / "app.py"
    app.write_text(
        _ROUTE + '\n\n@app.delete("/users/{user_id}")\ndef drop(user_id: int):\n    pass\n'
    )

    assert _run(mono).extracted
    assert any(c["contract_id"].startswith("http::DELETE") for c in _store(mono)["contracts"])
    assert not _run(mono).extracted  # same dirty bytes

    app.write_text(_ROUTE)  # back to the commit
    assert _run(mono).extracted
    assert not any(c["contract_id"].startswith("http::DELETE") for c in _store(mono)["contracts"])


def test_an_uncommitted_edit_to_a_non_contract_file_reuses(mono: Path) -> None:
    _run(mono)
    (mono / "README.md").write_text("# work in progress\n")
    (mono / "notes.txt").write_text("untracked\n")

    assert not _run(mono).extracted


def test_a_new_service_marker_re_extracts(mono: Path) -> None:
    _run(mono)
    (mono / "web" / "src" / "go.mod").write_text("module x\n")

    assert _run(mono).extracted


def test_outside_git_every_run_extracts(tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    (repo / "api").mkdir(parents=True)
    (repo / "api" / "app.py").write_text(_ROUTE)

    assert _run(repo).extracted
    assert _run(repo).extracted


def test_an_edit_to_an_ignored_service_name_file_re_extracts(mono: Path) -> None:
    (mono / ".gitignore").write_text(".env\n")
    _commit(mono, "ignore env")
    (mono / ".env").write_text("API_URL=http://api:8000\n")
    _run(mono)
    assert not _run(mono).extracted

    (mono / ".env").write_text("API_URL=http://users:8000\n")

    assert _run(mono).extracted


def test_a_first_source_file_of_a_boundary_only_language_re_extracts(mono: Path) -> None:
    _run(mono)
    (mono / "web" / "src" / "Main.scala").write_text("object Main\n")

    assert _run(mono).extracted


def test_rows_read_before_the_index_existed_are_read_again(
    mono: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(mono)  # no wiki.db yet
    assert not _run(mono).extracted
    monkeypatch.setattr(repo_contracts, "_has_index", lambda _p: True)

    assert _run(mono).extracted


def test_a_damaged_file_is_re_extracted_quietly(
    mono: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _run(mono)
    repo_contracts_path(mono).write_text("{bad", encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert _run(mono).extracted
    assert not [r for r in caplog.records if r.levelname in ("WARNING", "ERROR")]
    assert _store(mono)["contracts"]


def test_the_repos_exclude_patterns_keep_a_folder_out(mono: Path) -> None:
    _run(mono)
    config = mono / ".repowise" / "config.yaml"
    config.write_text("exclude_patterns:\n  - services/\n")

    assert _run(mono).extracted  # the patterns key reuse
    files = {c["file_path"] for c in _store(mono)["contracts"]}
    assert files == {"web/src/api.ts"}
    assert not _store(mono)["contract_links"]


def test_a_wiki_db_without_a_repository_row_reads_without_it(
    mono: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.workspace import repo_index

    async def _no_row(*_a, **_k):
        raise LookupError("No repository row")

    monkeypatch.setattr(repo_index, "open_repo_index", _no_row)
    monkeypatch.setattr(repo_contracts, "_has_index", lambda _p: True)

    assert _run(mono).extracted
    assert _store(mono)["contract_links"]
    assert not _run(mono).extracted  # not re-read every run
