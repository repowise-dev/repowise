"""Batch arity and preview on ``repowise decision confirm`` / ``dismiss``.

The dev store holds hundreds of candidates, so the properties that matter here
are that one refusal does not end the run, that a preview writes nothing, and
that the single-id document these verbs already emitted is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli

from .test_decision_cmd import _seed_wiki_db

_GOOD = "a" * 32
_ALSO_GOOD = "b" * 32
#: No rationale and no scope, so the acceptance contract refuses it.
_BARE = "c" * 32


@pytest.fixture
def review_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".repowise").mkdir()
    _seed_wiki_db(
        repo,
        [
            {"id": _GOOD, "title": "Use JWT", "status": "proposed", "source": "pr"},
            {"id": _ALSO_GOOD, "title": "Use Postgres", "status": "proposed", "source": "pr"},
            {
                "id": _BARE,
                "title": "Bare",
                "status": "proposed",
                "source": "pr",
                "rationale": "",
                "decision": "",
                "affected_files": [],
            },
        ],
    )
    return repo


def _run(repo: Path, *args: str):
    return CliRunner().invoke(cli, ["decision", *args, str(repo), "--format", "json"])


def _lanes(repo: Path) -> dict:
    result = _run(repo, "status")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["lanes"]


def test_batch_confirm_applies_the_rest_after_a_refusal(review_repo: Path) -> None:
    result = _run(review_repo, "confirm", "aaaa", "cccc", "bbbb")

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["succeeded"] == 2
    assert payload["failed"] == 1
    by_id = {r["given"]: r for r in payload["results"]}
    assert by_id["aaaa"]["action"] == "accepted"
    assert by_id["bbbb"]["action"] == "accepted"
    assert by_id["cccc"]["error"] == "acceptance_refused"
    assert any("scope" in blocker for blocker in by_id["cccc"]["blockers"])
    # The refused id is the middle one: the two either side still landed.
    assert _lanes(review_repo)["governing"] == 2


def test_a_refused_id_leaves_no_edit_behind(review_repo: Path) -> None:
    """``accept_decision`` edits scope before the contract can refuse.

    Without a savepoint per id the refused record would keep the edit that
    the accepted ids commit alongside them.
    """
    result = _run(review_repo, "confirm", "aaaa", "cccc", "--scope", "src/edited.py")

    assert result.exit_code == 1, result.output
    shown = CliRunner().invoke(cli, ["decision", "show", "cccc", str(review_repo), "--format", "json"])
    assert "src/edited.py" not in shown.output


def test_preview_writes_nothing(review_repo: Path) -> None:
    result = _run(review_repo, "confirm", "aaaa", "bbbb", "--preview")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preview"] is True
    assert [r["action"] for r in payload["results"]] == ["would_accept", "would_accept"]
    assert _lanes(review_repo)["governing"] == 0


def test_preview_reports_a_refusal_without_writing(review_repo: Path) -> None:
    result = _run(review_repo, "confirm", "cccc", "--preview")

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["results"][0]["error"] == "acceptance_refused"
    assert _lanes(review_repo)["candidates"] == 3


def test_one_id_keeps_the_transition_document(review_repo: Path) -> None:
    result = _run(review_repo, "confirm", "aaaa")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {"id": _GOOD, "status": "active", "action": "accepted"}


def test_batch_dismiss_reports_an_unknown_id_beside_the_rest(review_repo: Path) -> None:
    result = _run(review_repo, "dismiss", "cccc", "zzzz", "--yes")

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["succeeded"] == 1
    by_id = {r["given"]: r for r in payload["results"]}
    assert by_id["cccc"]["action"] == "dismissed"
    assert by_id["zzzz"]["error"] == "decision_not_found"


def test_the_trailing_path_still_resolves_with_many_ids(review_repo: Path, tmp_path: Path) -> None:
    """A path is a positional after the ids, and only a real directory is one."""
    runner = CliRunner()
    result = runner.invoke(
        cli, ["decision", "confirm", "aaaa", "bbbb", str(review_repo), "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["succeeded"] == 2


def test_an_ambiguous_single_id_refuses_instead_of_crashing(tmp_path: Path) -> None:
    """An ambiguous prefix resolves to no record, so there is no id to name."""
    repo = tmp_path / "ambiguous"
    repo.mkdir()
    (repo / ".repowise").mkdir()
    _seed_wiki_db(
        repo,
        [
            {"id": "dd" + "1" * 30, "title": "One", "status": "proposed"},
            {"id": "dd" + "2" * 30, "title": "Two", "status": "proposed"},
        ],
    )

    result = _run(repo, "confirm", "dd")

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["error"] == "ambiguous_id"
    assert "decision_id" not in payload
    assert "ambiguous" in payload["message"]


def test_an_id_shaped_directory_stays_an_id(review_repo: Path, monkeypatch) -> None:
    """Reading a hex token as a path would drop it from the batch and exit 0."""
    monkeypatch.chdir(review_repo.parent)
    (review_repo.parent / "cccc").mkdir()

    result = CliRunner().invoke(
        cli, ["decision", "confirm", "aaaa", "cccc", "--preview", "--format", "json"]
    )

    payload = json.loads(result.output)
    assert [r["given"] for r in payload["results"]] == ["aaaa", "cccc"]
