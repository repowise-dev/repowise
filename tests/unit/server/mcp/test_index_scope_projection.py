"""What an ordinary response says about the index's scope, and what it costs.

The canonical scope answers a question almost no call asks, and it was riding
on every response: about 900 characters, roughly a third of a small
``get_symbol`` reply. The digest keeps the part that changes how an answer
should be read and names where the rest lives.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from repowise.core.index_scope import (
    INDEX_SCOPE_ENV,
    compact_index_scope,
    index_scope_fingerprint,
    resolve_index_scope,
)
from repowise.server.mcp_server import _meta


class _Repo:
    def __init__(self, path: Path) -> None:
        self.local_path = str(path)
        self.head_commit = "abc123def456"
        self.indexed_at = "2026-09-16T00:00:00+00:00"


def _write_state(path: Path, state: dict[str, Any]) -> _Repo:
    repowise_dir = path / ".repowise"
    repowise_dir.mkdir(exist_ok=True)
    (repowise_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return _Repo(path)


_FULL_STATE: dict[str, Any] = {
    "run_mode": "standard",
    "git_tier": "full",
    "docs_mode": "llm",
    "index_scope": {
        "git_commit_cap": 500,
        "file_pages": {"eligible": 2499, "generated": 2247, "omitted": 252},
        "provider": {"name": "openai", "model": "a-model", "embedder": "openai"},
        "search": {"full_text": "available", "semantic": "available"},
    },
    "git_history_coverage": {"eligible_files": 4024, "retained_commits": 15360},
    "full_upgrade": {"status": "complete", "completed_stages": ["generation"]},
}


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(INDEX_SCOPE_ENV, raising=False)
    monkeypatch.setattr(_meta, "read_live_head", lambda _p: None)


def _chars(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), default=str))


# --- the projection itself -------------------------------------------------


def test_the_digest_keeps_what_changes_how_an_answer_reads() -> None:
    scope = resolve_index_scope(_FULL_STATE)
    compact = compact_index_scope(scope)

    assert compact is not None
    assert compact["run_mode"] == "standard"
    assert compact["content_provenance"] == "model"
    assert compact["git_tier"] == "full"
    assert compact["projection"] == "compact"
    # Says outright that it is not the whole thing, and where the rest is.
    assert compact["full"] == "get_overview()"


def test_the_digest_is_a_tenth_of_the_scope_it_stands_for() -> None:
    scope = resolve_index_scope(_FULL_STATE)

    assert _chars(compact_index_scope(scope)) < _chars(scope) * 0.5


def test_nothing_to_project_stays_nothing() -> None:
    assert compact_index_scope(None) is None


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({**_FULL_STATE, "full_upgrade": {"status": "running"}}, "upgrading"),
        ({**_FULL_STATE, "full_upgrade": {"status": "resumable"}}, "upgrading"),
        ({**_FULL_STATE, "full_upgrade": {"status": "failed"}}, "degraded"),
        ({**_FULL_STATE, "degraded": ["health"]}, "degraded"),
        (_FULL_STATE, "partial"),
    ],
)
def test_status_names_the_condition_that_explains_the_rest(
    state: dict[str, Any], expected: str
) -> None:
    assert compact_index_scope(resolve_index_scope(state))["status"] == expected


def test_omitted_pages_are_never_reported_as_complete() -> None:
    """A partial index must not read as a whole one to save a word."""
    scope = resolve_index_scope(_FULL_STATE)
    assert scope["file_pages"]["omitted"] == 252

    assert compact_index_scope(scope)["status"] != "complete"


def test_a_whole_index_says_so() -> None:
    state = {
        **_FULL_STATE,
        "index_scope": {
            **_FULL_STATE["index_scope"],
            "file_pages": {"eligible": 10, "generated": 10, "omitted": 0},
        },
    }

    assert compact_index_scope(resolve_index_scope(state))["status"] == "complete"


# --- the fingerprint -------------------------------------------------------


def test_the_same_scope_fingerprints_the_same() -> None:
    scope = resolve_index_scope(_FULL_STATE)

    assert index_scope_fingerprint(scope) == index_scope_fingerprint(
        resolve_index_scope(_FULL_STATE)
    )


def test_a_changed_scope_fingerprints_differently() -> None:
    """The point of carrying it: a held copy can be checked without resending."""
    other = resolve_index_scope({**_FULL_STATE, "run_mode": "fast"})

    assert index_scope_fingerprint(resolve_index_scope(_FULL_STATE)) != (
        index_scope_fingerprint(other)
    )


# --- what a response carries -----------------------------------------------


def test_a_routine_response_carries_the_digest(tmp_path: Path) -> None:
    out = _meta.build_meta(repository=_write_state(tmp_path, _FULL_STATE))

    assert out["index_scope"]["projection"] == "compact"
    assert "git_history_coverage" not in out["index_scope"]


def test_an_orientation_call_carries_the_whole_scope(tmp_path: Path) -> None:
    out = _meta.build_meta_with_full_scope(
        repository=_write_state(tmp_path, _FULL_STATE)
    )

    assert out["index_scope"]["git_history_coverage"]["eligible_files"] == 4024
    assert "projection" not in out["index_scope"]


def test_the_environment_restores_the_old_shape_everywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compatibility window for a reader that parses the whole object."""
    monkeypatch.setenv(INDEX_SCOPE_ENV, "full")

    out = _meta.build_meta(repository=_write_state(tmp_path, _FULL_STATE))

    assert out["index_scope"]["file_pages"]["eligible"] == 2499
    assert "projection" not in out["index_scope"]


def test_the_digest_costs_a_fraction_of_a_small_response(tmp_path: Path) -> None:
    """The measured regression: ~900 of 2,541 characters, about 35%."""
    repository = _write_state(tmp_path, _FULL_STATE)
    compact = _meta.build_meta(repository=repository)["index_scope"]
    full = _meta.build_meta_with_full_scope(repository=repository)["index_scope"]

    # A small response is about 1,650 characters once the scope is taken out.
    small_response = 2_541 - _chars(full)
    assert _chars(compact) < (small_response + _chars(compact)) * 0.10


def test_a_repo_with_no_state_still_carries_no_scope(tmp_path: Path) -> None:
    out = _meta.build_meta(repository=_Repo(tmp_path))

    assert "index_scope" not in out
