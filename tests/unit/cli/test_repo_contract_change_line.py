"""The update line on how the repo's own contracts moved (``contracts.in_repo``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands import _index_outcome
from repowise.core.workspace.repo_contracts import repo_contracts_path

_USER_ROUTE = '@app.get("/users/{user_id}")\ndef get_user(user_id: int):\n    return {}\n'
_ORDER_ROUTE = '@app.post("/orders")\ndef create_order():\n    return {}\n'
_HEAD = "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"

_CALL = """export async function loadUser(id: string) {
  const res = await fetch(`/users/${id}`);
  return res.json();
}
"""


@pytest.fixture
def mono(tmp_path: Path) -> Path:
    """One repo, two services: a FastAPI api and a web client that calls it.

    Outside git, so every run re-extracts and the diff is always taken.
    """
    repo = tmp_path / "mono"
    (repo / ".repowise").mkdir(parents=True)
    (repo / "services" / "api").mkdir(parents=True)
    (repo / "web" / "src").mkdir(parents=True)
    (repo / "services" / "api" / "pyproject.toml").write_text('[project]\nname = "api"\n')
    (repo / "services" / "api" / "app.py").write_text(_HEAD + _USER_ROUTE)
    (repo / "web" / "package.json").write_text('{"name": "web"}\n')
    (repo / "web" / "src" / "api.ts").write_text(_CALL)
    return repo


def _turn_on(repo: Path, extra: str = "") -> None:
    (repo / ".repowise" / "config.yaml").write_text("contracts:\n  in_repo: true\n" + extra)


def _routes(repo: Path, *routes: str) -> None:
    (repo / "services" / "api" / "app.py").write_text(_HEAD + "\n\n".join(routes))


def _update(repo: Path, capsys: pytest.CaptureFixture[str]) -> str:
    capsys.readouterr()
    _index_outcome.refresh_repo_contracts(repo)
    return capsys.readouterr().out


def test_a_first_run_prints_no_change_line(mono: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _turn_on(mono)
    assert not repo_contracts_path(mono).exists()

    out = _update(mono, capsys)

    assert "Contracts:" in out
    assert "contract change" not in out


def test_a_removed_route_another_service_calls_names_both_services(
    mono: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _turn_on(mono)
    _update(mono, capsys)
    _routes(mono, _ORDER_ROUTE)  # /users/{user_id} gone, /orders new

    out = _update(mono, capsys)

    assert "2 contract change(s); 1 break another service:" in out
    [line] = [ln for ln in out.splitlines() if "removed endpoint" in ln]
    assert "/users/" in line
    assert line.rstrip().endswith("services/api -> web")


def test_a_renamed_route_breaks_its_caller(mono: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _turn_on(mono)
    _update(mono, capsys)
    _routes(mono, _USER_ROUTE.replace("/users/", "/members/"))

    out = _update(mono, capsys)

    assert "1 break another service" in out
    assert "services/api -> web" in out


def test_an_added_route_breaks_nothing(mono: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _turn_on(mono)
    _update(mono, capsys)
    _routes(mono, _USER_ROUTE, _ORDER_ROUTE)

    out = _update(mono, capsys)

    assert "1 contract change(s); none breaks another service." in out
    assert "break another service:" not in out


def test_an_unchanged_re_extraction_prints_no_change_line(
    mono: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _turn_on(mono)
    _update(mono, capsys)

    out = _update(mono, capsys)

    assert "Contracts:" in out  # re-extracted: outside git every run does
    assert "contract change" not in out


def test_rows_read_under_other_settings_are_not_diffed(
    mono: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _turn_on(mono)
    _update(mono, capsys)
    # Excluding the api drops its route: a change in what is read, not the code.
    _turn_on(mono, "exclude_patterns:\n  - services/\n")

    out = _update(mono, capsys)

    assert "Contracts:" in out
    assert "contract change" not in out


def test_off_a_contract_change_prints_nothing(
    mono: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _turn_on(mono)
    _update(mono, capsys)
    (mono / ".repowise" / "config.yaml").write_text("contracts:\n  in_repo: false\n")
    _routes(mono, _ORDER_ROUTE)
    before = repo_contracts_path(mono).read_bytes()

    assert _update(mono, capsys) == ""
    assert repo_contracts_path(mono).read_bytes() == before
