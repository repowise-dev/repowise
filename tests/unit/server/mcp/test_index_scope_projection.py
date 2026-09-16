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


_WHOLE_PAGES = {"eligible": 10, "generated": 10, "omitted": 0}


def _scope_with(**overrides: Any) -> dict[str, Any]:
    """A canonical scope from the full fixture, with sub-blocks replaced."""
    return resolve_index_scope(
        {**_FULL_STATE, "index_scope": {**_FULL_STATE["index_scope"], **overrides}}
    )


def _status_of(**overrides: Any) -> str:
    return compact_index_scope(_scope_with(**overrides))["status"]


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


def test_the_digest_is_a_fraction_of_the_scope_it_stands_for() -> None:
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
    assert _status_of(file_pages=_WHOLE_PAGES) == "complete"


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


def test_the_digest_stays_under_a_fixed_ceiling(tmp_path: Path) -> None:
    """The measured regression was ~900 characters on every response.

    A fixed ceiling rather than a ratio against the canonical object: tying
    the digest's budget to the size of the thing it replaced would tighten
    this bar every time a diagnostic is added to that object, and fail a test
    about the digest for a reason that has nothing to do with it.
    """
    compact = _meta.build_meta(repository=_write_state(tmp_path, _FULL_STATE))

    assert _chars(compact["index_scope"]) <= 300


def test_a_repo_with_no_state_still_carries_no_scope(tmp_path: Path) -> None:
    out = _meta.build_meta(repository=_Repo(tmp_path))

    assert "index_scope" not in out


# --- absence of evidence is not completeness -------------------------------


def test_an_index_that_never_recorded_its_coverage_says_unknown() -> None:
    """The invariant: a missing result is never reported as a clean result.

    Every field the fold reads projects as ``unknown``/``None`` for a legacy
    index, and reading that as "nothing is wrong" would launder silence into
    a claim of completeness.
    """
    assert compact_index_scope(resolve_index_scope({}))["status"] == "unknown"


def test_an_index_with_no_scope_key_says_unknown() -> None:
    state = {"run_mode": "standard", "git_tier": "full", "docs_mode": "llm"}

    assert compact_index_scope(resolve_index_scope(state))["status"] == "unknown"


def _legs(**override: str) -> dict[str, str]:
    return {"full_text": "available", "semantic": "available", **override}


@pytest.mark.parametrize("leg", ["full_text", "semantic"])
def test_an_unavailable_search_leg_is_degraded(leg: str) -> None:
    assert _status_of(search=_legs(**{leg: "unavailable"})) == "degraded"


@pytest.mark.parametrize("leg", ["full_text", "semantic"])
def test_a_pending_search_leg_is_partial(leg: str) -> None:
    """Pending means not usable yet — smaller than failed, larger than fine."""
    status = _status_of(file_pages=_WHOLE_PAGES, search=_legs(**{leg: "pending"}))

    assert status == "partial"


def test_skipped_analysis_is_partial() -> None:
    status = _status_of(
        file_pages=_WHOLE_PAGES, analysis={"skipped": ["performance"]}
    )

    assert status == "partial"


def test_a_degraded_index_names_what_degraded() -> None:
    """"health failed" and "the graph failed" are not the same warning."""
    state = {**_FULL_STATE, "degraded": ["health"]}

    compact = compact_index_scope(resolve_index_scope(state))

    assert compact["status"] == "degraded"
    assert compact["degraded_analyses"] == ["health"]


def test_a_sound_index_carries_no_degraded_names() -> None:
    assert "degraded_analyses" not in compact_index_scope(
        resolve_index_scope(_FULL_STATE)
    )


# --- the fingerprint has to be on both shapes ------------------------------


def test_the_held_copy_carries_the_fingerprint_too(tmp_path: Path) -> None:
    """Checking a held copy needs the fingerprint on the copy, not only the
    digest that would be compared against it."""
    repository = _write_state(tmp_path, _FULL_STATE)

    compact = _meta.build_meta(repository=repository)["index_scope"]
    full = _meta.build_meta_with_full_scope(repository=repository)["index_scope"]

    assert full["fingerprint"] == compact["fingerprint"]


def test_the_fingerprint_refuses_what_it_cannot_canonicalise() -> None:
    """A silent str() fallback would churn the digest every process."""
    with pytest.raises(TypeError):
        index_scope_fingerprint({"version": 1, "odd": object()})


# --- where the rest actually lives -----------------------------------------


def test_the_pointer_names_the_repo_argument_in_workspace_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_overview() with no repo returns the listing, which carries no scope."""
    from repowise.server.mcp_server import _state

    monkeypatch.setattr(_state, "_registry", object(), raising=False)

    out = _meta.build_meta(repository=_write_state(tmp_path, _FULL_STATE))

    assert out["index_scope"]["full"] == "get_overview(repo=...)"


def test_the_pointer_is_a_plain_call_for_a_single_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.server.mcp_server import _state

    monkeypatch.setattr(_state, "_registry", None, raising=False)

    out = _meta.build_meta(repository=_write_state(tmp_path, _FULL_STATE))

    assert out["index_scope"]["full"] == "get_overview()"


def test_the_window_opens_without_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client spawns this server; it cannot be told to start again."""
    repository = _write_state(tmp_path, _FULL_STATE)
    assert _meta.build_meta(repository=repository)["index_scope"]["projection"]

    monkeypatch.setenv(INDEX_SCOPE_ENV, "full")

    assert "projection" not in _meta.build_meta(repository=repository)["index_scope"]
