"""``repowise decision discover`` prints the prompt the dashboard copies."""

from __future__ import annotations

import json

from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.core.agent_prompts.decisions import render_add
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
