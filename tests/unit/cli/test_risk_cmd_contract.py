"""What `repowise risk` puts on stdout, pinned so a refactor cannot move it.

The existing tests here cover exclusion rules and the independent-changes
section. What they do not cover is the *contract*: the exact JSON key set a
script parses, the `--target` path and its projection, and the errors a caller
scripts against. All of that is what a caller depends on and none of it was
protected, so this file pins it before the orchestration underneath it moves.

Deliberately assertions about key SETS rather than values. A value that drifts
with the repository under test says nothing; a key that disappears breaks a
caller silently.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.risk_cmd import _DROPPED_TARGET_KEYS, project_risk, risk_command


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(["add", relative_path], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-m", message], repo)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A two-commit repository with a scoreable second commit."""
    path = tmp_path / "repo"
    path.mkdir()
    _git(["init", "-q"], path)
    _commit(path, {"README.md": "# seed\n"}, "chore: seed")
    _commit(
        path,
        {"src/app.py": "value = 1\n", "src/util.py": "def helper():\n    return 2\n"},
        "feat: app",
    )
    return path


def _json(repo: Path, *args: str) -> dict:
    result = CliRunner().invoke(
        risk_command, ["HEAD", "--path", str(repo), "--baseline", "0", *args]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# The JSON a script parses
# ---------------------------------------------------------------------------

#: Every key the revspec path has always emitted. Removing one is a breaking
#: change for any caller reading it; adding one is not, so this is a subset
#: assertion in the test below rather than an equality.
_REVSPEC_KEYS = {
    "ref",
    "working_tree",
    "fix_history",
    "risk_authority",
    "score",
    "score_measures",
    "score_unit",
    "risk_percentile",
    "review_priority",
    "classification",
    "fallback_band",
    "baseline_sample_size",
    "exclude_patterns",
    "risk_scales",
    "is_fix",
    "features",
    "drivers",
}


def test_the_revspec_json_keeps_every_key_a_caller_reads(repo: Path) -> None:
    payload = _json(repo, "--format", "json")

    assert set(payload) >= _REVSPEC_KEYS


def test_the_feature_vector_keeps_its_names(repo: Path) -> None:
    """The Kamei metric names are the contract, not an implementation detail."""
    payload = _json(repo, "--format", "json")

    assert set(payload["features"]) == {"la", "ld", "nf", "nd", "ns", "entropy", "exp"}


def test_the_fix_history_block_keeps_its_shape(repo: Path) -> None:
    payload = _json(repo, "--format", "json")

    assert set(payload["fix_history"]) == {"available", "density", "percentile", "files"}
    assert isinstance(payload["fix_history"]["available"], bool)


def test_every_driver_row_keeps_its_keys(repo: Path) -> None:
    payload = _json(repo, "--format", "json")

    assert payload["drivers"]
    for driver in payload["drivers"]:
        assert set(driver) == {"feature", "value", "contribution", "label"}


def test_the_json_path_always_ships_scales(repo: Path) -> None:
    """Unlike the MCP tool, the CLI sends units every time; a script has no
    second call to ask with."""
    payload = _json(repo, "--format", "json")

    assert payload["risk_scales"]


def test_full_forces_json_rather_than_printing_a_table(repo: Path) -> None:
    """Silently handing a script that asked for the full payload a rich table
    and exiting 0 is the failure this guards."""
    result = CliRunner().invoke(
        risk_command, ["HEAD", "--path", str(repo), "--baseline", "0", "--full"]
    )

    assert result.exit_code == 0, result.output
    json.loads(result.output)


def test_an_unrankable_change_says_so_in_both_fields(repo: Path) -> None:
    """With no baseline there is no percentile, and the band takes over."""
    payload = _json(repo, "--format", "json")

    assert payload["risk_percentile"] is None
    assert payload["review_priority"] is None
    assert payload["fallback_band"] in {"low", "moderate", "high"}


# ---------------------------------------------------------------------------
# The projection behind --target
# ---------------------------------------------------------------------------


def test_the_target_projection_drops_exactly_what_it_documents() -> None:
    payload = {
        "targets": {
            "a.py": {
                "risk_summary": "hot",
                "dependents_count": 3,
                "_base_dep_count": 9,
                "impact_surface": ["b.py", "c.py"],
            }
        }
    }

    projected = project_risk(payload)

    card = projected["targets"]["a.py"]
    assert set(card) == {"risk_summary", "dependents_count"}
    for dropped in _DROPPED_TARGET_KEYS:
        assert dropped not in card


def test_the_projection_keeps_the_blocks_a_caller_cannot_get_elsewhere() -> None:
    """``pr_blast_radius`` survives because ``recommended_reviewers`` has no
    substitute anywhere else in the response."""
    payload = {
        "directive": {"status": "review"},
        "targets": {},
        "pr_blast_radius": {"recommended_reviewers": ["dev"]},
        "global_hotspots": [{"path": "a.py"}],
        "risk_scales": [{"unit": "items"}],
        "omission_marker": "repowise#abc",
    }

    projected = project_risk(payload)

    assert set(projected) >= {
        "directive",
        "targets",
        "pr_blast_radius",
        "global_hotspots",
        "risk_scales",
        "omission_marker",
    }


def test_an_absent_block_is_omitted_rather_than_nulled() -> None:
    """A caller checks presence, so an empty block must not become a null one."""
    projected = project_risk({"targets": {}})

    assert "directive" not in projected
    assert "pr_blast_radius" not in projected
    assert projected["targets"] == {}


def test_the_projection_survives_a_payload_with_no_targets_key() -> None:
    assert project_risk({})["targets"] == {}


# ---------------------------------------------------------------------------
# Errors a caller scripts against
# ---------------------------------------------------------------------------


def test_changed_file_without_target_is_a_usage_error(repo: Path) -> None:
    result = CliRunner().invoke(
        risk_command, ["--path", str(repo), "--changed-file", "src/app.py"]
    )

    assert result.exit_code != 0
    assert "--target" in result.output


def test_an_unreadable_revspec_fails_cleanly_rather_than_tracing(repo: Path) -> None:
    result = CliRunner().invoke(
        risk_command, ["no-such-ref", "--path", str(repo), "--baseline", "0"]
    )

    assert result.exit_code != 0
    assert "Could not read change" in result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# The human path's load-bearing phrases
# ---------------------------------------------------------------------------


def test_the_table_says_the_score_measures_shape_not_danger(repo: Path) -> None:
    """The one caveat that stops the 0-10 being read as a probability."""
    result = CliRunner().invoke(risk_command, ["HEAD", "--path", str(repo), "--baseline", "0"])

    assert result.exit_code == 0, result.output
    assert "Diff-size score" in result.output
    assert "not where it lands" in result.output


def test_a_clean_fix_record_is_stated_rather_than_left_blank(repo: Path) -> None:
    result = CliRunner().invoke(risk_command, ["HEAD", "--path", str(repo), "--baseline", "0"])

    assert "no bug-fix history in the files it touches" in result.output
