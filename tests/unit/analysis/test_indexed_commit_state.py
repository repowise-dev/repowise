"""``_indexed_commit`` reads ``state.json`` without pulling the pipeline stack.

``test_collection._indexed_commit`` is on the cold ``impacted-tests`` path:
it used to import ``read_state_commit`` from ``workspace.update``, whose
module-level ``PhaseTimingRecorder`` import dragged in the whole
``repowise.core.pipeline`` package (~1s per cold call). The reader now lives
in the light ``repowise.core.workspace.state`` module; these tests pin the
read behaviour and the import-time contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from repowise.core.analysis.test_collection import _indexed_commit

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_SRC = REPO_ROOT / "packages" / "core" / "src"


def _repo_with_state(tmp_path: Path, payload: str | None) -> Path:
    """A fake repo whose ``.repowise/state.json`` holds *payload* (or is absent)."""
    repo = tmp_path / "repo"
    if payload is not None:
        (repo / ".repowise").mkdir(parents=True)
        (repo / ".repowise" / "state.json").write_text(payload, encoding="utf-8")
    return repo


def test_indexed_commit_prefers_state_json_over_row(tmp_path):
    repo = _repo_with_state(tmp_path, json.dumps({"last_sync_commit": "abc123"}))
    out: dict = {}
    _indexed_commit(str(repo), "abc123", out)
    assert out == {"indexed_commit": "abc123"}


def test_indexed_commit_flags_state_row_disagreement(tmp_path):
    repo = _repo_with_state(tmp_path, json.dumps({"last_sync_commit": "abc123"}))
    out: dict = {}
    _indexed_commit(str(repo), "def456", out)
    assert "index_problem" in out
    assert "abc123"[:7] in out["index_problem"]


def test_indexed_commit_missing_state_falls_back_to_row(tmp_path):
    repo = _repo_with_state(tmp_path, None)
    out: dict = {}
    _indexed_commit(str(repo), "def456", out)
    assert out == {"indexed_commit": "def456"}


def test_indexed_commit_non_dict_state_is_none(tmp_path):
    repo = _repo_with_state(tmp_path, json.dumps([1, 2, 3]))
    out: dict = {}
    _indexed_commit(str(repo), None, out)
    assert out == {"indexed_commit": None}


def test_indexed_commit_does_not_import_pipeline():
    """Importing ``test_collection`` and calling ``_indexed_commit`` must leave
    ``repowise.core.pipeline`` out of ``sys.modules`` — the regression the
    move to ``workspace.state`` exists to prevent."""
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(CORE_SRC)}
    code = (
        "import sys; "
        "import repowise.core.analysis.test_collection as t; "
        "t._indexed_commit('.', None, {}); "
        "assert 'repowise.core.pipeline' not in sys.modules, "
        "[m for m in sys.modules if 'pipeline' in m]; "
        "print('no pipeline import')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-1000:]
    assert "no pipeline import" in result.stdout
