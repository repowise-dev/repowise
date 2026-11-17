"""``repowise decision discover`` prints the prompt the dashboard copies."""

from __future__ import annotations

import json

from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.core.agent_prompts.decisions import render_add
from tests.unit.cli.test_decision_candidates_review import _add_git
from tests.unit.cli.test_decision_cmd import _seed_wiki_db


def test_text_is_the_prompt_as_is(tmp_path):
    _seed_wiki_db(tmp_path, [])

    result = CliRunner().invoke(cli, ["decision", "discover", str(tmp_path)])

    assert result.exit_code == 0, result.output
    # No background records: the pass records the decisions instead.
    assert result.output == render_add() + "\n"


def test_json_carries_kind_ids_and_text(tmp_path):
    _seed_wiki_db(tmp_path, [])

    result = CliRunner().invoke(cli, ["decision", "discover", str(tmp_path), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"kind": "add", "text": render_add(), "ids": [], "count": 0}


def test_agreements_with_none_stored_say_so(tmp_path):
    _seed_wiki_db(tmp_path, [])

    result = CliRunner().invoke(cli, ["decision", "discover", str(tmp_path), "--agreements"])

    assert result.exit_code == 0, result.output
    assert "No working agreements are stored." in result.output


def test_all_prints_the_whole_queue_prompt(tmp_path):
    from repowise.core.agent_prompts.decisions import render_review_all

    _seed_wiki_db(tmp_path, [{"id": "c" * 32, "title": "Keep it", "status": "proposed"}])
    _add_git(tmp_path, ["src/app.py"])

    result = CliRunner().invoke(cli, ["decision", "discover", str(tmp_path), "--all"])

    assert result.exit_code == 0, result.output
    assert result.output == render_review_all() + "\n"


def test_all_with_nothing_left_says_so(tmp_path):
    _seed_wiki_db(tmp_path, [])

    result = CliRunner().invoke(cli, ["decision", "discover", str(tmp_path), "--all"])

    assert result.exit_code == 0, result.output
    assert "No background records left." in result.output


def test_all_skips_agreements_pending_and_unscoped_records(tmp_path):
    _seed_wiki_db(
        tmp_path,
        [
            {"id": "a" * 32, "title": "Agreement", "status": "proposed", "kind": "agreement"},
            {"id": "b" * 32, "title": "Pending", "status": "proposed", "source": "agent"},
            {"id": "c" * 32, "title": "No file", "status": "proposed", "affected_files": []},
        ],
    )
    _add_git(tmp_path, ["src/app.py"])

    result = CliRunner().invoke(
        cli, ["decision", "discover", str(tmp_path), "--all", "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["text"] == ""


def test_all_and_agreements_are_one_or_the_other(tmp_path):
    _seed_wiki_db(tmp_path, [])

    result = CliRunner().invoke(
        cli, ["decision", "discover", str(tmp_path), "--all", "--agreements"]
    )

    assert result.exit_code != 0 and "pass one" in result.output
