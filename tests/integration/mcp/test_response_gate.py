"""Regression gate for the default MCP tool responses.

Indexes the sample repo (no model, mock embedder), then calls each default
tool through the real server the way an agent does. Per call it asserts the
response stays under its byte ceiling, the lean field contract of
docs/reference/MCP_RESPONSE_FIELDS.md holds, and a few get_answer grades hold.
No snapshots of whole responses and no timing asserts.

Ceilings live in tests/fixtures/mcp/response_budgets.json. After an intended
size change, re-bless them (measured size plus 15%) and commit the diff:
``REPOWISE_REWRITE_GOLDEN=1 pytest tests/integration/mcp/test_response_gate.py``
"""

from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pydantic_core
import pytest

from tests.unit.server.conftest import normalize_payload, path_placeholders

_BUDGETS = Path(__file__).parents[2] / "fixtures" / "mcp" / "response_budgets.json"
_HEADROOM = 1.15

_INIT = (
    "--no-prose",
    "--embedder",
    "mock",
    "--no-editor-setup",
    "--no-hook",
    "--no-agents",
    "--no-codex",
    "--no-claude-md",
    "--no-workspace",
    "--no-distill-hook",
    "--no-cost-tracking",
    "-y",
)

# (case id, tool, arguments, top-level keys the response must carry). Order is
# call order: once-per-process fields land on the first call that can carry them.
_CALLS: list[tuple[str, str, dict[str, Any], tuple[str, ...]]] = [
    ("overview", "get_overview", {}, ("title", "key_modules", "_meta")),
    (
        "answer_defined_many_times",
        "get_answer",
        {"question": "Where is the Calculator class defined?"},
        ("answer", "confidence", "_meta"),
    ),
    (
        "answer_not_in_repo",
        "get_answer",
        {"question": "How does the payment refund webhook retry failed charges?"},
        ("answer", "confidence", "_meta"),
    ),
    ("context_file", "get_context", {"targets": ["python_pkg/calculator.py"]}, ("targets",)),
    ("context_dir", "get_context", {"targets": ["python_pkg"]}, ("targets",)),
    (
        "context_symbol",
        "get_context",
        {"targets": ["python_pkg/calculator.py::Calculator"]},
        ("targets",),
    ),
    (
        "symbol",
        "get_symbol",
        {"symbol_id": "python_pkg/calculator.py::Calculator"},
        ("source", "symbol_id"),
    ),
    ("search_identifier", "search_codebase", {"query": "Calculator"}, ("lines", "results")),
    (
        "search_concept",
        "search_codebase",
        {"query": "how are calculation results formatted"},
        ("results",),
    ),
    ("search_path", "search_codebase", {"query": "python_pkg/models.py"}, ("results",)),
    ("risk", "get_risk", {"targets": ["python_pkg/calculator.py"]}, ("targets",)),
    ("change_risk", "get_change_risk", {"revspec": "HEAD"}, ("directive",)),
    (
        "change_risk_per_commit",
        "get_change_risk",
        {"revspec": "HEAD~1..HEAD", "per_commit": True},
        ("commits", "review"),
    ),
    ("why", "get_why", {"query": "why does the calculator keep a history"}, ("mode",)),
    (
        "why_targeted",
        "get_why",
        {"query": "why does the calculator keep a history", "targets": ["python_pkg/calculator.py"]},
        ("mode", "decisions"),
    ),
    ("health", "get_health", {}, ("mode", "kpis")),
    ("dead_code", "get_dead_code", {}, ("summary", "tiers")),
]
_IDS = [case[0] for case in _CALLS]

#: get_answer grades that must hold: a name with several definitions is not
#: answered with certainty, and a question the repo cannot answer is graded low.
_GRADE_NOT = {"answer_defined_many_times": "high"}
_GRADE_IS = {"answer_not_in_repo": "low"}


def _copy_lf(src: str, dst: str) -> None:
    # A Windows checkout has CRLF sources; sizes must match the Linux CI run.
    Path(dst).write_bytes(Path(src).read_bytes().replace(b"\r\n", b"\n"))


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _build_repo(src: Path, dest: Path) -> None:
    """The sample repo in two commits, so get_change_risk has a diff to score."""
    shutil.copytree(src, dest, copy_function=_copy_lf)
    _git(dest, "init", "-q")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "init")
    calc = dest / "python_pkg" / "calculator.py"
    calc.write_text(
        calc.read_text(encoding="utf-8") + "\n\ndef triple(x: float) -> float:\n    return x * 3\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(dest, "commit", "-q", "-am", "add triple")


def _index(repo: Path) -> None:
    from click.testing import CliRunner

    from repowise.cli.helpers import release_update_lock
    from repowise.cli.main import cli

    try:
        result = CliRunner().invoke(cli, ["init", str(repo), *_INIT], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        release_update_lock(repo)


async def _call_all(repo: Path) -> dict[str, dict[str, Any]]:
    from mcp.shared.memory import create_connected_server_and_client_session

    from repowise.server.mcp_server import create_mcp_server

    server = create_mcp_server(str(repo), workspace_mode=False)
    served: dict[str, dict[str, Any]] = {}
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        for case_id, tool, args, _ in _CALLS:
            result = await client.call_tool(tool, args)
            assert not result.isError, f"{case_id}: {result.content}"
            served[case_id] = json.loads(result.content[0].text)
    return served


@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory, sample_repo_path: Path) -> dict:
    """Every case's delivered payload, temp paths replaced by one placeholder."""
    from repowise.core.upgrade import release
    from repowise.core.workspace.config import WORKSPACE_CEILING_ENV_VAR

    root = tmp_path_factory.mktemp("mcp_gate")
    repo = root / "repo"
    _build_repo(sample_repo_path, repo)
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("REPOWISE_DB_URL", raising=False)
        mp.delenv("REPOWISE_DATABASE_URL", raising=False)
        mp.delenv("REPOWISE_MCP_DEBUG_META", raising=False)
        mp.delenv("REPOWISE_MCP_PRETTY_JSON", raising=False)
        mp.setenv("DO_NOT_TRACK", "1")
        mp.setenv(WORKSPACE_CEILING_ENV_VAR, str(root))
        # A newer PyPI release would add a one-off note to the first response.
        mp.setattr(
            release,
            "check_latest_version_cached",
            lambda current, **_: release.ReleaseCheck(current, None, None, None),
        )
        from repowise.server.mcp_server import _state

        # Another test in the same process may have seen or announced a release.
        mp.setattr(_state, "_release_check", None, raising=False)
        mp.setattr(_state, "_release_announced", None, raising=False)
        _index(repo)
        payloads = asyncio.run(_call_all(repo))
    return normalize_payload(payloads, path_placeholders(repo))


def _size(payload: dict) -> int:
    # The compact text block the server sends (see ``wire_result``).
    return len(pydantic_core.to_json(payload, fallback=str))


def _read_budgets() -> dict[str, int]:
    return json.loads(_BUDGETS.read_text(encoding="utf-8")) if _BUDGETS.exists() else {}


@pytest.fixture(scope="module")
def ceilings(served: dict) -> dict[str, int]:
    if os.environ.get("REPOWISE_REWRITE_GOLDEN") == "1":
        blessed = {case: math.ceil(_size(served[case]) * _HEADROOM) for case in _IDS}
        _BUDGETS.write_text(json.dumps(blessed, indent=2) + "\n", encoding="utf-8", newline="\n")
    return _read_budgets()


@pytest.mark.parametrize("case_id", _IDS)
def test_response_stays_under_its_byte_ceiling(served, ceilings, case_id) -> None:
    assert case_id in ceilings, f"no ceiling for {case_id}; re-bless (see module docstring)"
    size = _size(served[case_id])
    assert size <= ceilings[case_id], (
        f"{case_id} grew to {size} bytes, over its ceiling of {ceilings[case_id]}. "
        "Trim the response, or re-bless if the growth is intended (see module docstring)."
    )


def test_every_case_has_a_ceiling_and_no_stale_ones() -> None:
    assert sorted(_read_budgets()) == sorted(_IDS)


@pytest.mark.parametrize("case_id", _IDS)
def test_response_carries_its_contract_fields(served, case_id) -> None:
    required = next(case[3] for case in _CALLS if case[0] == case_id)
    missing = [key for key in required if key not in served[case_id]]
    assert not missing, f"{case_id} lost {missing}"
    assert "_meta" in served[case_id]


@pytest.mark.parametrize("case_id", [c for c in _IDS if c != "overview"])
def test_routine_response_is_lean(served, case_id) -> None:
    """Debug envelope fields stay out, and the lean pass has nothing left to drop."""
    from repowise.server.mcp_server._meta import (
        _DEBUG_ACCOUNTING_KEYS,
        _DEBUG_FRESHNESS_KEYS,
        lean_response,
    )

    payload = served[case_id]
    debug = {"contract_version", "timing_ms", *_DEBUG_FRESHNESS_KEYS, *_DEBUG_ACCOUNTING_KEYS}
    assert not debug & set(payload["_meta"]), f"{case_id} leaks {debug & set(payload['_meta'])}"
    assert lean_response(copy.deepcopy(payload)) == payload


def test_why_with_no_decision_is_a_compact_verdict(served) -> None:
    """No record governs the file: the verdict and its best evidence, not the file's card."""
    payload = served["why_targeted"]
    assert payload["decisions"] == []
    assert "target_context" not in payload and "try_instead" not in payload
    assert len(payload.get("code_rationale") or []) <= 3
    assert len(payload.get("key_commits") or []) <= 2
    assert _size(payload) < 3000


def test_overview_carries_the_full_envelope(served) -> None:
    from repowise.server.mcp_server._meta import MCP_CONTRACT_VERSION

    meta = served["overview"]["_meta"]
    assert meta["contract_version"] == MCP_CONTRACT_VERSION
    assert "timing_ms" in meta


@pytest.mark.parametrize("case_id", sorted({*_GRADE_NOT, *_GRADE_IS}))
def test_answer_grade_holds(served, case_id) -> None:
    grade = served[case_id]["confidence"]
    if case_id in _GRADE_IS:
        assert grade == _GRADE_IS[case_id], f"{case_id} graded {grade}"
    if case_id in _GRADE_NOT:
        assert grade != _GRADE_NOT[case_id], f"{case_id} graded {grade}"
