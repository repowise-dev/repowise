"""``repowise mark ID STATUS``: text and --json, and every refusal with a non-zero exit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.unit.cli.test_report_first_look_cmd import indexed  # noqa: F401  (fixture)


def _cli(*args: str):
    import inspect

    from repowise.cli.main import cli

    # Keep stderr out of stdout on click 8.1 (8.2 always separates them).
    split = "mix_stderr" in inspect.signature(CliRunner.__init__).parameters
    runner = CliRunner(mix_stderr=False) if split else CliRunner()
    return runner.invoke(cli, list(args))


def _items(root: Path) -> dict[str, dict]:
    result = _cli("report", "repo", str(root), "--format", "json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout[result.stdout.index("{") :])
    return {i["rule"]: i for s in doc["sections"] for i in s["items"]}


def test_marks_an_item_and_the_report_shows_it(indexed: Path) -> None:  # noqa: F811
    cycle = _items(indexed)["import_cycle"]

    result = _cli("mark", cycle["id"], "ack", "--reason", "generated pair", "--path", str(indexed))
    assert result.exit_code == 0, result.output
    assert f"Marked {cycle['id']} acknowledged." in result.stdout

    result = _cli("mark", cycle["id"], "snooze", "--days", "3", "--json", "--path", str(indexed))
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert set(out) == {"id", "store", "status", "reason", "until"}
    assert out["id"] == cycle["id"] and out["status"] == "snoozed" and out["store"] == "item"
    assert out["until"] and out["reason"] is None
    assert _items(indexed)["import_cycle"]["status"] == "snoozed"

    result = _cli(
        "mark", cycle["id"], "ack", "--reason", "generated pair", "--json", "--path", str(indexed)
    )
    out = json.loads(result.stdout)
    assert (out["status"], out["reason"], out["until"]) == ("acknowledged", "generated pair", None)
    assert _items(indexed)["import_cycle"]["status_reason"] == "generated pair"


@pytest.mark.parametrize(
    ("rule", "args", "code"),
    [
        ("hardcoded_password", ["false-positive", "--reason", "test value"], "security_use_ignore"),
        ("import_cycle", ["ack"], "reason_required"),
        (None, ["done"], "unknown_item"),
    ],
)
def test_refusals_exit_non_zero_with_a_code(indexed: Path, rule, args, code) -> None:  # noqa: F811
    item_id = _items(indexed)[rule]["id"] if rule else "fl_000000000000"
    as_json = _cli("mark", item_id, *args, "--json", "--path", str(indexed))
    assert as_json.exit_code == 1
    assert as_json.stdout == ""  # stdout carries only a result
    error = json.loads(as_json.stderr)["error"]
    assert error["code"] == code and error["message"]
    if code == "unknown_item":
        assert "fl_ followed by 12 hex characters" in error["message"]

    text = _cli("mark", item_id, *args, "--path", str(indexed))
    assert text.exit_code == 1
    assert error["message"].split(".")[0] in " ".join(text.stderr.split())


def test_days_is_for_snooze_only(indexed: Path) -> None:  # noqa: F811
    result = _cli("mark", "fl_000000000000", "done", "--days", "3", "--path", str(indexed))
    assert result.exit_code == 2 and "--days applies to snooze only" in result.stderr


def test_without_an_index_it_says_so(tmp_path: Path) -> None:
    result = _cli("mark", "fl_000000000000", "done", "--json", "--path", str(tmp_path))
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["code"] == "no_index"
