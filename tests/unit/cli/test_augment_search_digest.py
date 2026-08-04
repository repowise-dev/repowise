"""Tests for serving a grep flood as its digest (``updatedToolOutput``).

The Read surface shipped this same mechanism twice before it worked: once
emitting a bare string against Read's object schema, which Claude Code rejected
silently while the ledger went on recording served rows, and once behind a flag
no code path could write. Both failures were invisible to a test that only
checked "did the handler return something". So the tests here pin the *wire
shape* and the *capability gate*, not just the decision.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from repowise.cli.commands import augment_cmd
from repowise.cli.commands.augment_cmd import search_digest
from repowise.cli.commands.augment_cmd.search import _handle_search_post

# A real multi-file flood: 60 matches over 6 files, the shape `group_search_matches`
# parses. Mirrors the captured `mode: "content"` payload exactly.
_FLOOD_TEXT = "\n".join(
    f"packages/core/src/repowise/core/mod{i % 6}.py:{100 + i}:    result = compute_value(x, y)  # match {i}"
    for i in range(60)
)

GREP_CONTENT_FLOOD = {
    "mode": "content",
    "numFiles": 6,
    "filenames": [],
    "content": _FLOOD_TEXT,
    "numLines": 60,
    "totalLines": 60,
}


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".repowise").mkdir()
    return tmp_path


@pytest.fixture
def opted_in(repo):
    (repo / ".repowise" / "config.yaml").write_text(
        "hooks:\n  search_digest: true\n", encoding="utf-8"
    )
    return repo


def _fire(cwd, tool_output=None, client=None, pattern="compute_value"):
    return _handle_search_post(
        tool_name="Grep",
        tool_input={"pattern": pattern},
        tool_output=tool_output if tool_output is not None else dict(GREP_CONTENT_FLOOD),
        cwd=str(cwd),
        client=client,
    )


class TestReplacementWireShape:
    """What Claude Code will actually accept for a Grep."""

    def test_replacement_is_grep_shaped_not_a_bare_string(self, opted_in) -> None:
        result = _fire(opted_in)
        assert result.replacement is not None, "the flood should have been replaced"
        assert isinstance(result.replacement, dict), (
            "updatedToolOutput is validated against Grep's own schema; a bare "
            "string is rejected silently and the agent sees the original flood"
        )
        assert result.replacement["mode"] == "content"
        assert "content" in result.replacement

    def test_replacement_carries_unknown_keys_through(self, opted_in) -> None:
        payload = dict(GREP_CONTENT_FLOOD, appliedLimit=60, someFutureKey="keep me")
        result = _fire(opted_in, tool_output=payload)
        assert result.replacement["appliedLimit"] == 60
        assert result.replacement["someFutureKey"] == "keep me"

    def test_numlines_matches_the_served_content(self, opted_in) -> None:
        result = _fire(opted_in)
        content = result.replacement["content"]
        assert result.replacement["numLines"] == content.count("\n") + 1

    def test_served_content_is_the_digest_and_names_its_files(self, opted_in) -> None:
        content = _fire(opted_in).replacement["content"]
        assert "[repowise]" in content
        assert "mod0.py" in content
        # The reversibility contract: counts and anchor line numbers survive.
        assert "matches)" in content
        assert "L1" in content

    def test_the_digest_does_not_also_ride_along_as_context(self, opted_in) -> None:
        """Replacing and appending the same text would bill it twice."""
        result = _fire(opted_in)
        assert result.context is None


class TestCapabilityGate:
    def test_codex_never_gets_a_replacement(self, opted_in) -> None:
        """Codex's hook protocol has no updatedToolOutput field.

        Sending one would be dropped on the floor while the ledger recorded a
        served row: the exact failure the Read surface shipped twice.
        """
        result = _fire(opted_in, client="codex")
        assert result.replacement is None
        assert result.context is not None, "Codex still gets the digest, appended"
        assert "matches in" in result.context

    def test_unknown_client_is_treated_as_claude(self, opted_in) -> None:
        assert _fire(opted_in, client=None).replacement is not None

    def test_old_client_build_falls_back_to_appending(self, opted_in) -> None:
        with patch.object(
            augment_cmd.search_digest, "replaces_tool_output", return_value=True
        ), patch(
            "repowise.cli.commands.augment_cmd.read_skeleton.supports_updated_output",
            return_value=False,
        ):
            result = _fire(opted_in)
        assert result.replacement is None
        assert result.context is not None


class TestOptIn:
    def test_off_by_default_the_digest_still_appends(self, repo) -> None:
        result = _fire(repo)
        assert result.replacement is None
        assert result.context is not None

    def test_env_override_enables_it(self, repo, monkeypatch) -> None:
        monkeypatch.setenv("REPOWISE_HOOK_SEARCH_DIGEST", "1")
        assert _fire(repo).replacement is not None

    def test_env_override_disables_it(self, opted_in, monkeypatch) -> None:
        monkeypatch.setenv("REPOWISE_HOOK_SEARCH_DIGEST", "0")
        assert _fire(opted_in).replacement is None


class TestWorthItGates:
    def test_files_with_matches_is_never_replaced(self, opted_in) -> None:
        """No content field to stand in for, and the file list is already a digest."""
        payload = {
            "mode": "files_with_matches",
            "filenames": [f"src/mod{i}.py" for i in range(60)],
            "numFiles": 60,
            "totalFiles": 60,
        }
        assert _fire(opted_in, tool_output=payload).replacement is None

    def test_a_flood_of_one_line_matches_saves_too_little_to_replace(self, opted_in) -> None:
        """60 one-character matches: the digest is no smaller than the flood."""
        text = "\n".join(f"src/mod{i}.py:{i}:x" for i in range(60))
        payload = dict(GREP_CONTENT_FLOOD, content=text)
        result = _fire(opted_in, tool_output=payload)
        assert result.replacement is None, "replacing here would be a detour, not a saving"
        assert result.context is not None, "the digest still appends, as it always did"

    def test_an_oversized_digest_is_skipped_not_truncated(self) -> None:
        """A cut digest loses the tail that says what was dropped."""
        oversized = "y" * (search_digest.MAX_OUTPUT_CHARS + 1000)
        assert search_digest.digest_replacement("p", "z" * 500_000, oversized) is None

    def test_a_digest_that_is_not_much_smaller_is_not_served(self) -> None:
        assert search_digest.digest_replacement("p", "z" * 4000, "y" * 3000) is None

    def test_a_digest_well_under_the_flood_is_served(self) -> None:
        made = search_digest.digest_replacement("p", "z" * 8000, "y" * 2000)
        assert made is not None
        assert made.saved_tokens > 0


class TestAsGrepOutput:
    def test_rejects_non_content_modes(self) -> None:
        assert search_digest.as_grep_output({"mode": "files_with_matches"}, "x") is None
        assert search_digest.as_grep_output({"filenames": []}, "x") is None

    def test_rejects_non_dict(self) -> None:
        assert search_digest.as_grep_output("a string", "x") is None
        assert search_digest.as_grep_output(None, "x") is None

    def test_rejects_content_mode_without_content(self) -> None:
        assert search_digest.as_grep_output({"mode": "content", "numLines": 3}, "x") is None

    def test_leaves_search_facts_alone(self) -> None:
        out = search_digest.as_grep_output(dict(GREP_CONTENT_FLOOD), "one\ntwo")
        assert out["totalLines"] == GREP_CONTENT_FLOOD["totalLines"]
        assert out["numFiles"] == GREP_CONTENT_FLOOD["numFiles"]
        assert out["numLines"] == 2


class TestReplacesToolOutput:
    @pytest.mark.parametrize("client", [None, "claude", "claude-code"])
    def test_clients_that_can_replace(self, client) -> None:
        assert search_digest.replaces_tool_output(client) is True

    def test_codex_cannot(self) -> None:
        assert search_digest.replaces_tool_output("codex") is False
