"""Tests for the PostToolUse Grep/Glob smart-enrichment decision tree.

The hook is designed to be silent on the common case (focused search
already returned what the agent wanted) and only speak up when it can
add information the raw result didn't carry. These tests pin the
boundary cases of the decision tree without hitting the wiki — the
``_search_enrich`` async path is mocked because it requires a real
wiki.db, and is exercised end-to-end in integration tests instead.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from repowise.cli.commands import augment_cmd
from repowise.cli.commands.augment_cmd import (
    _count_search_results,
    _extract_output_text,
    _handle_search_post,
    _looks_like_path_lookup,
    _name_variants,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestLooksLikePathLookup:
    @pytest.mark.parametrize(
        "pattern",
        [
            "src/auth/service.py",
            "packages/web/src",
            "*.py",
            "**/*.tsx",
            "init_cmd.py",
            "README.md",
            r"C:\Users\ragha\Desktop\repowise",
        ],
    )
    def test_path_style_skips(self, pattern: str) -> None:
        assert _looks_like_path_lookup(pattern) is True

    @pytest.mark.parametrize(
        "pattern",
        [
            "parse_yaml",
            "GraphBuilder",
            "auth",
            "session",
            "use cache",
            "fooBar",
        ],
    )
    def test_concept_queries_do_not_skip(self, pattern: str) -> None:
        assert _looks_like_path_lookup(pattern) is False


class TestExtractOutputText:
    def test_string_passthrough(self) -> None:
        assert _extract_output_text("hello\nworld") == "hello\nworld"

    def test_dict_with_output_key(self) -> None:
        assert _extract_output_text({"output": "x\ny"}) == "x\ny"

    def test_dict_with_stdout_key(self) -> None:
        assert _extract_output_text({"stdout": "z"}) == "z"

    def test_dict_with_text_list(self) -> None:
        assert (
            _extract_output_text(
                {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
            )
            == "a\nb"
        )

    def test_unrecognised_shape(self) -> None:
        assert _extract_output_text(None) == ""
        assert _extract_output_text(42) == ""
        assert _extract_output_text({"unrelated": "value"}) == ""


class TestCountSearchResults:
    def test_empty(self) -> None:
        assert _count_search_results("") == 0
        assert _count_search_results("   \n  ") == 0

    @pytest.mark.parametrize(
        "text",
        [
            "No matches found",
            "no files found",
            "Found 0 files",
            "Found 0 matches",
        ],
    )
    def test_zero_markers(self, text: str) -> None:
        assert _count_search_results(text) == 0

    def test_strips_found_header(self) -> None:
        text = "Found 3 files\nfile1.py\nfile2.py\nfile3.py"
        assert _count_search_results(text) == 3

    def test_counts_nonempty_lines(self) -> None:
        text = "src/a.py\n\nsrc/b.py\n  \nsrc/c.py"
        assert _count_search_results(text) == 3


class TestNameVariants:
    def test_snake_to_camel(self) -> None:
        v = _name_variants("parse_yaml")
        assert "parse_yaml" in v
        assert any(x.lower() == "parseyaml" for x in v)
        assert "parseYaml" in v

    def test_camel_to_snake(self) -> None:
        v = _name_variants("parseYaml")
        assert any(x == "parse_yaml" for x in v)

    def test_pascal_to_snake(self) -> None:
        v = _name_variants("ParseYaml")
        assert any(x == "parse_yaml" for x in v)

    def test_empty_input(self) -> None:
        assert _name_variants("") == []
        assert _name_variants("___") == []


# ---------------------------------------------------------------------------
# Decision tree — the gating logic before any wiki query runs
# ---------------------------------------------------------------------------


@pytest.fixture
def repowise_cwd(tmp_path):
    """A cwd with a ``.repowise`` directory so ``_find_repo_root`` succeeds."""
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def _call(tool_name, pattern, output_text, cwd):
    return _handle_search_post(
        tool_name=tool_name,
        tool_input={"pattern": pattern},
        tool_output={"output": output_text},
        cwd=str(cwd),
    )


class TestDecisionTree:
    def test_skip_when_pattern_is_path(self, repowise_cwd) -> None:
        with patch.object(augment_cmd, "_search_enrich") as enrich:
            assert _call("Grep", "src/foo.py", "src/foo.py:1: x", repowise_cwd) is None
            enrich.assert_not_called()

    def test_skip_when_no_pattern(self, repowise_cwd) -> None:
        with patch.object(augment_cmd, "_search_enrich") as enrich:
            assert (
                _handle_search_post(
                    tool_name="Grep",
                    tool_input={"pattern": ""},
                    tool_output={"output": "anything"},
                    cwd=str(repowise_cwd),
                )
                is None
            )
            enrich.assert_not_called()

    def test_skip_when_outside_repowise_repo(self, tmp_path) -> None:
        # No .repowise dir: silently skip without invoking the enrich path.
        with patch.object(augment_cmd, "_search_enrich") as enrich:
            assert _call("Grep", "auth", "src/a.py:1: hit", tmp_path) is None
            enrich.assert_not_called()

    def test_skip_on_focused_result_set(self, repowise_cwd) -> None:
        """1–14 lines = focused: agent has what it wanted, hook stays silent."""
        output = "\n".join(f"src/file{i}.py:1: hit" for i in range(5))
        with patch.object(augment_cmd, "_search_enrich") as enrich:
            assert _call("Grep", "auth", output, repowise_cwd) is None
            enrich.assert_not_called()

    def test_rescue_mode_on_zero_results(self, repowise_cwd) -> None:
        """0 lines + concept query → rescue mode."""
        sentinel = object()
        with patch.object(
            augment_cmd, "_search_enrich", return_value=sentinel
        ) as enrich:
            with patch("asyncio.run", side_effect=lambda coro: (coro.close(), sentinel)[1]):
                _call("Grep", "parse_yaml", "", repowise_cwd)
            (call_args,) = enrich.call_args_list
            kwargs = call_args.kwargs or {}
            args = call_args.args
            mode = kwargs.get("mode") if "mode" in kwargs else args[2]
            assert mode == "rescue"

    def test_triage_mode_on_flood(self, repowise_cwd) -> None:
        """>= _TRIAGE_THRESHOLD lines → triage mode."""
        from repowise.cli.commands.augment_cmd import _TRIAGE_THRESHOLD

        big = "\n".join(f"src/file{i}.py:1: hit" for i in range(_TRIAGE_THRESHOLD + 5))
        sentinel = object()
        with patch.object(
            augment_cmd, "_search_enrich", return_value=sentinel
        ) as enrich:
            with patch("asyncio.run", side_effect=lambda coro: (coro.close(), sentinel)[1]):
                _call("Grep", "auth", big, repowise_cwd)
            call_args = enrich.call_args_list[0]
            args = call_args.args
            kwargs = call_args.kwargs or {}
            mode = kwargs.get("mode") if "mode" in kwargs else args[2]
            assert mode == "triage"


# ---------------------------------------------------------------------------
# Grep-flood compact digest (>= _DIGEST_THRESHOLD lines)
# ---------------------------------------------------------------------------


def _flood(files: int, per_file: int) -> str:
    return "\n".join(
        f"src/file{i}.py:{j}:hit number {j}" for i in range(files) for j in range(1, per_file + 1)
    )


class TestFloodDigest:
    def test_digest_on_big_flood(self, repowise_cwd) -> None:
        """>= _DIGEST_THRESHOLD parseable lines across >= 3 files → digest."""
        out = _call("Grep", "auth", _flood(files=8, per_file=10), repowise_cwd)
        assert out is not None
        assert "compact digest" in out
        # No wiki.db in the fixture repo → ordering falls back to match count.
        assert "match count" in out
        assert "80 matches in 8 files" in out

    def test_digest_fires_even_for_path_patterns(self, repowise_cwd) -> None:
        """The digest summarizes results, so the concept-vs-path gate is moot."""
        out = _call("Grep", "src/file0.py", _flood(files=8, per_file=10), repowise_cwd)
        assert out is not None and "compact digest" in out

    def test_few_files_fall_through_to_triage(self, repowise_cwd) -> None:
        """A flood concentrated in 1-2 files is already navigable — no digest."""
        sentinel = "triaged"
        with patch.object(augment_cmd, "_search_enrich", return_value=sentinel) as enrich:
            with patch("asyncio.run", side_effect=lambda coro: (coro.close(), sentinel)[1]):
                out = _call("Grep", "auth", _flood(files=2, per_file=40), repowise_cwd)
            assert out == sentinel
            assert enrich.called

    def test_unparseable_flood_falls_through(self, repowise_cwd) -> None:
        """Glob-style bare path lists can't be grouped — triage handles them."""
        big = "\n".join(f"line {i} of something unstructured" for i in range(60))
        sentinel = "triaged"
        with patch.object(augment_cmd, "_search_enrich", return_value=sentinel) as enrich:
            with patch("asyncio.run", side_effect=lambda coro: (coro.close(), sentinel)[1]):
                out = _call("Grep", "auth", big, repowise_cwd)
            assert out == sentinel
            assert enrich.called

    def test_top_files_listed_first(self, repowise_cwd) -> None:
        flood = "\n".join(
            [f"src/hot.py:{j}:hit" for j in range(1, 41)]
            + [f"src/warm.py:{j}:hit" for j in range(1, 11)]
            + [f"src/cold{i}.py:1:hit" for i in range(5)]
        )
        out = _call("Grep", "auth", flood, repowise_cwd)
        assert out is not None
        file_lines = [ln for ln in out.splitlines() if "matches)" in ln]
        assert file_lines[0].strip().startswith("src/hot.py")
