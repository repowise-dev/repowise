"""A commit-mined decision binds the files the model chose, not the commit's.

The defect these pin: ``pr`` and ``git_archaeology`` read one decision out of
one commit body and then took that commit's *whole* file list, because the
model was never asked which files the decision was about. A commit that
bundles three unrelated changes therefore produced three decisions each
claiming all of its files, and ``get_why`` on any of them answered with the
other two.

Measured out of sample on 29 records drawn from the dev store: the stored
commit lists are 27% on topic with 18% outright noise, and the same records
with the files chosen by a model reading the commit body are 93% on topic with
none noise, losing no record that had a real answer. Evidence in
``local-stash/decision-layer-research/capture-2026-09-19/RESULTS.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.analysis.decisions.commit_mining import pr_candidates, signal_commits
from repowise.core.analysis.decisions.extractor import (
    DecisionExtractor,
    _coerce_paths,
)
from repowise.core.analysis.decisions.scope import (
    SCOPE_BASIS_FOOTPRINT,
    SCOPE_BASIS_SELECTED,
    selected_scope_files,
)

#: One commit, two parts. ``_SUBJECT`` is what the decision under test is
#: about; the rest is what the same commit happened to touch.
_SUBJECT = "packages/core/src/repowise/core/hook.py"
_BYSTANDERS = [f"packages/core/src/repowise/core/mod{i:02d}.py" for i in range(8)]
_COMMIT_FILES = sorted([_SUBJECT, *_BYSTANDERS])

_SHA = "9a0b27a7"


def _meta_map(files: list[str] = _COMMIT_FILES) -> dict[str, dict]:
    """A ``_git_meta_map`` whose inversion yields one commit over *files*."""
    commit = {
        "sha": _SHA,
        "message": "perf: stop importing the workspace stack from the hook path",
        "subject": "perf: stop importing the workspace stack from the hook path",
        # Shaped so both miners accept it: the PR miner wants a PR-ish body
        # marker plus a decision signal, git archaeology wants the signal.
        "body": (
            "## Why\n\n"
            "The hook imported core.workspace.config only to reach "
            "find_workspace_root, which initialized the extractor stack. We "
            "chose instead to resolve CLI command modules lazily, because the "
            "import cost was charged to every invocation."
        ),
        "pr_number": 2439,
        "author": "someone",
        "date": "2026-09-01",
    }
    return {f: {"significant_commits_json": json.dumps([commit])} for f in files}


class _Provider:
    """Returns one canned decision payload, and records what it was asked."""

    def __init__(self, payload: list[dict]) -> None:
        self._payload = payload
        self.prompts: list[str] = []

    async def generate(self, system, prompt, **kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(content=json.dumps(self._payload))


def _extractor(tmp_path: Path, payload: list[dict]) -> DecisionExtractor:
    provider = _Provider(payload)
    ex = DecisionExtractor(
        repo_path=tmp_path, provider=provider, git_meta_map=_meta_map()
    )
    ex.test_provider = provider  # type: ignore[attr-defined]
    return ex


def _payload(**extra) -> list[dict]:
    return [
        {
            "commit_sha": _SHA,
            "title": "Avoid the workspace dependency graph in agent hooks",
            "decision": "Drop the module-level import from the hook path.",
            "rationale": "It initialized the whole extractor stack.",
            **extra,
        }
    ]


# --- the validator ---------------------------------------------------------


def test_selection_keeps_only_files_the_commit_touched():
    """A path outside the commit's list is a path the model invented.

    Intersecting is the whole of the validation: the miner has no other way to
    tell a real path from a plausible one, and the model is being shown the
    list it is meant to be choosing from.
    """
    kept = selected_scope_files([_SUBJECT, "packages/core/src/invented.py"], _COMMIT_FILES)
    assert kept == [_SUBJECT]


def test_selection_normalises_separators_before_comparing():
    """A Windows-shaped answer still matches a POSIX-shaped commit list."""
    assert selected_scope_files(
        ["packages\\core\\src\\repowise\\core\\hook.py"], _COMMIT_FILES
    ) == [_SUBJECT]


def test_selection_of_nothing_is_empty_rather_than_everything():
    assert selected_scope_files([], _COMMIT_FILES) == []


# --- the pr miner ----------------------------------------------------------


async def test_pr_binds_the_selected_file_and_not_the_commit(tmp_path):
    ex = _extractor(tmp_path, _payload(affected_files=[_SUBJECT]))

    (decision,) = await ex.mine_pr_bodies()

    assert decision.affected_files == [_SUBJECT]
    assert decision.scope_basis == SCOPE_BASIS_SELECTED
    for bystander in _BYSTANDERS:
        assert bystander not in decision.affected_files


async def test_pr_drops_a_path_the_commit_never_touched(tmp_path):
    ex = _extractor(
        tmp_path, _payload(affected_files=[_SUBJECT, "packages/core/src/ghost.py"])
    )

    (decision,) = await ex.mine_pr_bodies()

    assert decision.affected_files == [_SUBJECT]


async def test_pr_binds_nothing_when_the_model_selects_nothing(tmp_path):
    """An empty answer must not fall back to the commit's whole list.

    Falling back would reinstate exactly what this replaces, on the records it
    helps most: roughly one in six, every one of them measured as a record
    whose subject never reached the commit's file list at all.
    """
    ex = _extractor(tmp_path, _payload(affected_files=[]))

    (decision,) = await ex.mine_pr_bodies()

    assert decision.affected_files == []
    assert decision.scope_basis == SCOPE_BASIS_SELECTED


async def test_pr_falls_back_to_the_breadth_rule_when_the_key_is_absent(tmp_path):
    """A missing answer is not an empty one.

    A provider that has not seen the new prompt, or a cached response written
    before it existed, leaves the record scoped as it always was, under the
    basis that says so.
    """
    ex = _extractor(tmp_path, _payload())

    (decision,) = await ex.mine_pr_bodies()

    assert decision.affected_files == _COMMIT_FILES
    assert decision.scope_basis == SCOPE_BASIS_FOOTPRINT


async def test_pr_shows_the_model_the_commit_files(tmp_path):
    """The miner that scored worst is the one that never showed the list.

    ``mine_pr_bodies`` asked for a decision from a subject and a body alone,
    so "which files is this about" had nothing to be answered from. It scored
    20% on topic against git archaeology's 45% on the same store.
    """
    ex = _extractor(tmp_path, _payload(affected_files=[_SUBJECT]))

    await ex.mine_pr_bodies()

    (prompt,) = ex.test_provider.prompts  # type: ignore[attr-defined]
    assert "Files changed:" in prompt
    assert _SUBJECT in prompt


async def test_pr_carries_no_unvalidated_path_off_the_miner(tmp_path):
    """``proposed_files`` is cleared, so persistence cannot read it."""
    ex = _extractor(
        tmp_path, _payload(affected_files=[_SUBJECT, "packages/core/src/ghost.py"])
    )

    (decision,) = await ex.mine_pr_bodies()

    assert decision.proposed_files is None


# --- the git archaeology miner ---------------------------------------------


async def test_git_archaeology_binds_the_selected_file(tmp_path):
    ex = _extractor(tmp_path, _payload(affected_files=[_SUBJECT]))

    decisions = await ex.mine_git_archaeology()

    assert decisions, "the commit should have carried a decision signal"
    assert decisions[0].affected_files == [_SUBJECT]
    assert decisions[0].scope_basis == SCOPE_BASIS_SELECTED


async def test_git_archaeology_falls_back_when_the_key_is_absent(tmp_path):
    ex = _extractor(tmp_path, _payload())

    decisions = await ex.mine_git_archaeology()

    assert decisions[0].affected_files == _COMMIT_FILES
    assert decisions[0].scope_basis == SCOPE_BASIS_FOOTPRINT


async def test_git_archaeology_binds_nothing_when_the_model_selects_nothing(tmp_path):
    ex = _extractor(tmp_path, _payload(affected_files=[]))

    decisions = await ex.mine_git_archaeology()

    assert decisions[0].affected_files == []
    assert decisions[0].scope_basis == SCOPE_BASIS_SELECTED


async def test_git_archaeology_drops_a_path_the_commit_never_touched(tmp_path):
    ex = _extractor(
        tmp_path, _payload(affected_files=[_SUBJECT, "packages/core/src/ghost.py"])
    )

    decisions = await ex.mine_git_archaeology()

    assert decisions[0].affected_files == [_SUBJECT]


async def test_git_archaeology_shows_the_files_in_a_reproducible_order(tmp_path):
    """Which files the model may choose from is a property of the commit.

    The list is an inversion of a per-file map, so its natural order is
    whichever file the walk reached first. Truncating that unsorted made the
    choosable set depend on the index run rather than the commit.
    """
    ex = _extractor(tmp_path, _payload(affected_files=[_SUBJECT]))

    await ex.mine_git_archaeology()

    (prompt,) = ex.test_provider.prompts
    shown = prompt.split("Files changed: ")[1].split("\n")[0].split(", ")
    assert shown == sorted(shown)


# --- stored commit metadata in a shape the miners cannot use ---------------

_UNUSABLE_COMMIT_METADATA = {
    "none": None,
    "json_dict": json.dumps({"sha": _SHA, "message": "not a list"}),
    "json_list_of_non_dicts": json.dumps([1, "two", None]),
    "malformed_json": '[{"sha": ',
}


@pytest.mark.parametrize("value", _UNUSABLE_COMMIT_METADATA.values(), ids=_UNUSABLE_COMMIT_METADATA)
async def test_both_miners_read_unusable_commit_metadata_as_no_commits(tmp_path, value):
    """Git archaeology and the PR miner degrade alike instead of raising."""
    ex = _extractor(tmp_path, _payload(affected_files=[_SUBJECT]))
    ex._git_meta_map = {_SUBJECT: {"significant_commits_json": value}}

    assert await ex.mine_git_archaeology() == []
    assert await ex.mine_pr_bodies() == []
    assert ex.test_provider.prompts == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", _UNUSABLE_COMMIT_METADATA.values(), ids=_UNUSABLE_COMMIT_METADATA)
def test_one_unusable_file_does_not_hide_the_valid_ones(value):
    meta = {**_meta_map([_SUBJECT]), "packages/other.py": {"significant_commits_json": value}}

    commit_map, commit_files = signal_commits(meta)
    candidates, files_by_sha = pr_candidates(meta)

    assert list(commit_map) == [_SHA]
    assert commit_files == {_SHA: [_SUBJECT]}
    assert list(candidates) == [_SHA]
    assert files_by_sha == {_SHA: [_SUBJECT]}


# --- what the model returned, when it is not a list of paths ---------------


def test_a_malformed_answer_is_not_read_as_an_empty_selection():
    """A shape error must not permanently bind a record to nothing.

    ``[]`` means "about none of these files", which is a real answer and a
    common one. A list of dicts or numbers is a provider answering in the
    wrong shape, and reading it as ``[]`` would silently make the decision
    govern nothing for good. It falls back instead.
    """
    assert _coerce_paths([{"path": "a.py"}]) is None
    assert _coerce_paths([None]) is None
    assert _coerce_paths({"files": ["a.py"]}) is None
    # An answer that really is empty still reads as empty.
    assert _coerce_paths([]) == []


def test_a_bare_string_answer_is_read_as_one_path():
    assert _coerce_paths("a.py") == ["a.py"]


# --- a decision the miner cannot attribute to a commit ---------------------


async def test_an_unattributed_decision_keeps_no_model_paths(tmp_path):
    """No commit means no list to validate against, so the answer is unusable.

    Only the attributed branch called the helper that clears the field, so an
    unattributed decision carried the model's raw paths into
    ``dataclasses.asdict`` and on to persistence. Nothing there reads the key,
    so it was inert -- and the field promises that it cannot be read at all.
    """
    payload = _payload(affected_files=["packages/core/src/ghost.py"])
    del payload[0]["commit_sha"]
    payload[0]["title"] = "A title matching no commit subject"
    ex = _extractor(tmp_path, payload)

    (decision,) = await ex.mine_pr_bodies()

    assert decision.proposed_files is None
    assert decision.evidence_commits == []
    assert decision.affected_files == []


async def test_an_unattributed_archaeology_decision_keeps_no_model_paths(tmp_path):
    """The same guarantee on the other miner, which has the same branch."""
    payload = _payload(affected_files=["packages/core/src/ghost.py"])
    del payload[0]["commit_sha"]
    payload[0]["title"] = "A title matching no commit message"
    ex = _extractor(tmp_path, payload)

    decisions = await ex.mine_git_archaeology()

    assert decisions[0].proposed_files is None
    assert decisions[0].affected_files == []
