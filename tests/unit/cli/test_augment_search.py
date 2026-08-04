"""Tests for the PostToolUse Grep/Glob smart-enrichment decision tree.

The hook is designed to be silent on the common case (focused search
already returned what the agent wanted) and only speak up when it can
add information the raw result didn't carry. These tests pin the
boundary cases of the decision tree without hitting the wiki.

``_fast_search_enrich`` is the mocked seam because it is the one every
mode reaches first, including the zero-result rescue it declines to serve
and hands straight back to the ORM. Both lookup paths need a real wiki.db
and are exercised end-to-end in integration tests instead.
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
    _looks_like_regex,
    _name_variants,
    _search_result_count,
    _targets_single_non_code_file,
)
from repowise.cli.commands.augment_cmd.search import (
    _coverage_order,
    _matched_files,
    _pattern_terms,
    _rescue,
    _rrf,
    _triage,
)

# ---------------------------------------------------------------------------
# Grep/Glob tool_response fixtures — captured from real Claude Code
# PostToolUse payloads (transcript toolUseResult entries), trimmed to the
# fields the tool emits. The dict *shapes* are the contract under test.
# ---------------------------------------------------------------------------

GREP_FILES_MODE = {
    "mode": "files_with_matches",
    "filenames": [
        "packages\\web\\src\\lib\\api\\costs.ts",
        "packages\\web\\src\\app\\repos\\[id]\\costs\\page.tsx",
    ],
    "numFiles": 2,
}

GREP_FILES_MODE_ZERO = {"mode": "files_with_matches", "filenames": [], "numFiles": 0}

GREP_CONTENT_MODE = {
    "mode": "content",
    "numFiles": 0,
    "filenames": [],
    "content": "src/a.py:10:def parse_yaml():\nsrc/b.py:42:parse_yaml()",
    "numLines": 2,
}

GREP_CONTENT_MODE_ZERO = {
    "mode": "content",
    "numFiles": 0,
    "filenames": [],
    "content": "",
    "numLines": 0,
}

GREP_COUNT_MODE = {
    "mode": "count",
    "numFiles": 5,
    "filenames": [],
    "content": (
        "app\\services\\dodo_service.py:5\n"
        "app\\services\\admin_notifications.py:6\n"
        "app\\services\\embedding_service.py:13\n"
        "app\\services\\account_export_service.py:12\n"
        "app\\services\\github_app_service.py:9"
    ),
    "numMatches": 45,
    "appliedLimit": 5,
}

GLOB_RESPONSE = {
    "filenames": ["src\\a.py", "src\\b.py"],
    "durationMs": 12,
    "numFiles": 2,
    "truncated": False,
}

GLOB_RESPONSE_ZERO = {"filenames": [], "durationMs": 9769, "numFiles": 0, "truncated": False}

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

    def test_files_mode_joins_filenames(self) -> None:
        text = _extract_output_text(GREP_FILES_MODE)
        assert text.splitlines() == GREP_FILES_MODE["filenames"]

    def test_content_mode_returns_content(self) -> None:
        assert _extract_output_text(GREP_CONTENT_MODE) == GREP_CONTENT_MODE["content"]

    def test_glob_response_joins_filenames(self) -> None:
        assert _extract_output_text(GLOB_RESPONSE) == "src\\a.py\nsrc\\b.py"


class TestSearchResultCount:
    """Counting against the captured tool_response shapes, mode by mode."""

    def test_files_mode_counts_files(self) -> None:
        assert _search_result_count(GREP_FILES_MODE) == 2

    def test_files_mode_zero_is_a_true_zero(self) -> None:
        assert _search_result_count(GREP_FILES_MODE_ZERO) == 0

    def test_content_mode_counts_lines(self) -> None:
        assert _search_result_count(GREP_CONTENT_MODE) == 2

    def test_content_mode_zero_is_a_true_zero(self) -> None:
        assert _search_result_count(GREP_CONTENT_MODE_ZERO) == 0

    def test_count_mode_counts_matches(self) -> None:
        assert _search_result_count(GREP_COUNT_MODE) == 45

    def test_glob_counts_files(self) -> None:
        assert _search_result_count(GLOB_RESPONSE) == 2
        assert _search_result_count(GLOB_RESPONSE_ZERO) == 0

    def test_unknown_future_mode_is_unknown(self) -> None:
        assert _search_result_count({"mode": "summary", "summary": "3 hits"}) is None

    def test_unknown_shape_is_unknown_not_zero(self) -> None:
        assert _search_result_count({"unrelated": "value"}) is None
        assert _search_result_count(None) is None
        assert _search_result_count("") is None
        assert _search_result_count("   \n") is None

    def test_plain_text_counts_lines(self) -> None:
        assert _search_result_count("src/a.py:1: hit\nsrc/b.py:2: hit") == 2

    def test_text_zero_requires_a_sentinel(self) -> None:
        assert _search_result_count({"output": "No matches found"}) == 0
        assert _search_result_count("Found 0 files") == 0


class TestLooksLikeRegex:
    @pytest.mark.parametrize(
        "pattern",
        [
            "distill|savings",
            "def (parse|load)_yaml",
            r"\bparse_yaml\b",
            "parse.*yaml",
            "parse.+yaml",
            "[Pp]arse_yaml",
            "log(ger)?",
        ],
    )
    def test_regex_patterns_flag(self, pattern: str) -> None:
        assert _looks_like_regex(pattern) is True

    @pytest.mark.parametrize(
        "pattern",
        [
            "parse_yaml",
            "GraphBuilder",
            "use cache",
            r"escaped \| pipe",
            r"escaped \[ bracket",
            "v1.2",  # dot without a quantifier is fine
        ],
    )
    def test_literal_patterns_do_not_flag(self, pattern: str) -> None:
        assert _looks_like_regex(pattern) is False


class TestTargetsSingleNonCodeFile:
    @pytest.mark.parametrize(
        "tool_input",
        [
            {"pattern": "x", "path": "config/settings.yaml"},
            {"pattern": "x", "path": "package.json"},
            {"pattern": "x", "path": "README.md"},
            {"pattern": "x", "path": "pyproject.toml"},
            {"pattern": "x", "glob": "uv.lock"},
        ],
    )
    def test_single_non_code_targets(self, tool_input: dict) -> None:
        assert _targets_single_non_code_file(tool_input) is True

    @pytest.mark.parametrize(
        "tool_input",
        [
            {"pattern": "x"},  # repo-wide grep
            {"pattern": "x", "path": "src/auth/service.py"},  # code file
            {"pattern": "x", "path": "packages/core"},  # directory scope
            {"pattern": "x", "glob": "**/*.yaml"},  # many files
            {"pattern": "x", "glob": "*.json"},
        ],
    )
    def test_everything_else_stays_eligible(self, tool_input: dict) -> None:
        assert _targets_single_non_code_file(tool_input) is False


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
    """The appended-context leg of the handler, as a plain string or None.

    ``_handle_search_post`` returns a ``HookResult`` since the flood digest can
    replace the tool output; every test below this line is about what the hook
    *says*, so they read the context field and the replacement leg gets its own
    class.
    """
    return _handle_search_post(
        tool_name=tool_name,
        tool_input={"pattern": pattern},
        tool_output={"output": output_text},
        cwd=str(cwd),
    ).context


class TestDecisionTree:
    def test_skip_when_pattern_is_path(self, repowise_cwd) -> None:
        with patch.object(augment_cmd.search, "_fast_search_enrich") as enrich:
            assert _call("Grep", "src/foo.py", "src/foo.py:1: x", repowise_cwd) is None
            enrich.assert_not_called()

    def test_skip_when_no_pattern(self, repowise_cwd) -> None:
        with patch.object(augment_cmd.search, "_fast_search_enrich") as enrich:
            assert (
                _handle_search_post(
                    tool_name="Grep",
                    tool_input={"pattern": ""},
                    tool_output={"output": "anything"},
                    cwd=str(repowise_cwd),
                ).context
                is None
            )
            enrich.assert_not_called()

    def test_skip_when_outside_repowise_repo(self, tmp_path) -> None:
        """No .repowise dir: silently skip without invoking the enrich path.

        ``_find_repo_root`` is stubbed rather than relied on. A ``tmp_path``
        cwd is not in fact outside a repo under pytest: the suite patches
        ``HOME``, so ``_find_repo_root``'s "skip the user-level ``~/.repowise``"
        guard no longer recognises the real home and the walk resolves to it.
        Before the widened rescue this test passed anyway, on the focused-set
        skip below rather than on the branch it names.
        """
        with patch.object(augment_cmd.search, "_fast_search_enrich") as enrich:
            with patch.object(augment_cmd.search, "_find_repo_root", return_value=None):
                assert _call("Grep", "auth", "src/a.py:1: hit", tmp_path) is None
            enrich.assert_not_called()

    def test_focused_result_set_reaches_the_widened_rescue(self, repowise_cwd) -> None:
        """1–14 lines: the set-difference rescue gets a look (plan item 10).

        It is still usually silent, since the gate is inside ``_rescue`` and
        needs an exact symbol match in a file the grep did *not* return, but the
        mode is no longer skipped before the query.
        """
        output = "\n".join(f"src/file{i}.py:1: hit" for i in range(5))
        sentinel = object()
        with patch.object(
            augment_cmd.search, "_fast_search_enrich", return_value=sentinel
        ) as enrich:
            _call("Grep", "parse_yaml", output, repowise_cwd)
        args = enrich.call_args_list[0].args
        assert args[2] == "rescue_wide"
        # ...and it is handed the files grep actually matched, which is the
        # only thing the gate can be computed against.
        assert set(args[4]) == {f"src/file{i}.py" for i in range(5)}

    @pytest.mark.parametrize("pattern", ["coverage", "score", "provider", "layer_id"])
    def test_single_token_patterns_never_reach_the_widened_rescue(
        self, repowise_cwd, pattern: str
    ) -> None:
        """Every false positive in the transcript replay had this shape.

        "A symbol by that name is defined elsewhere" is true of half the repo
        for a bare `coverage` or `score`. The guard is also what keeps the
        query off the common path: it runs before any wiki lookup.
        """
        output = "\n".join(f"src/file{i}.py:1: hit" for i in range(5))
        with patch.object(augment_cmd.search, "_fast_search_enrich") as enrich:
            assert _call("Grep", pattern, output, repowise_cwd) is None
            enrich.assert_not_called()

    def test_focused_result_set_stays_silent_without_parseable_files(
        self, repowise_cwd
    ) -> None:
        """A result set whose files can't be read means no gate, so no rescue.

        The pattern clears the single-token guard on purpose, so this pins the
        parse gate rather than passing on the cheaper one above it.
        """
        output = "\n".join(f"unstructured line {i}" for i in range(5))
        with patch.object(augment_cmd.search, "_fast_search_enrich") as enrich:
            assert _call("Grep", "parse_yaml", output, repowise_cwd) is None
            enrich.assert_not_called()

    @pytest.mark.parametrize(
        "tool_output",
        [
            GREP_FILES_MODE_ZERO,
            GREP_CONTENT_MODE_ZERO,
            {"output": "No matches found"},
        ],
    )
    def test_rescue_mode_on_true_zero_results(self, repowise_cwd, tool_output) -> None:
        """A positively-identified zero + concept query → rescue mode."""
        sentinel = object()
        with patch.object(
            augment_cmd.search, "_fast_search_enrich", return_value=sentinel
        ) as enrich:
            _handle_search_post(
                tool_name="Grep",
                tool_input={"pattern": "parse_yaml"},
                tool_output=tool_output,
                cwd=str(repowise_cwd),
            )
            (call_args,) = enrich.call_args_list
            kwargs = call_args.kwargs or {}
            args = call_args.args
            mode = kwargs.get("mode") if "mode" in kwargs else args[2]
            assert mode == "rescue"

    def test_no_rescue_for_successful_files_mode_grep(self, repowise_cwd) -> None:
        """The live bug: files_with_matches results must never read as zero.

        Two matched files is a focused set, so this now reaches the *widened*
        rescue. What must never happen is the zero-result ``rescue``, whose
        text claims there was no literal match at all.
        """
        sentinel = object()
        with patch.object(
            augment_cmd.search, "_fast_search_enrich", return_value=sentinel
        ) as enrich:
            _handle_search_post(
                tool_name="Grep",
                tool_input={"pattern": "distill_savings"},
                tool_output=GREP_FILES_MODE,
                cwd=str(repowise_cwd),
            )
        assert enrich.call_args_list[0].args[2] == "rescue_wide"

    @pytest.mark.parametrize(
        "tool_output",
        [
            {"mode": "summary", "summary": "3 hits"},  # future Grep mode
            {"unrelated": "value"},
            {"output": ""},  # empty extraction is NOT a zero signal
            "",
        ],
    )
    def test_unknown_shape_skips_never_rescues(self, repowise_cwd, tool_output) -> None:
        with patch.object(augment_cmd.search, "_fast_search_enrich") as enrich:
            result = _handle_search_post(
                tool_name="Grep",
                tool_input={"pattern": "parse_yaml"},
                tool_output=tool_output,
                cwd=str(repowise_cwd),
            )
            assert not result
            enrich.assert_not_called()

    @pytest.mark.parametrize(
        "tool_input",
        [
            {"pattern": "distill|savings"},  # regex alternation, sanitizer-mangled
            {"pattern": r"\bdistill\b"},
            {"pattern": "default_model", "path": "config/settings.yaml"},
            {"pattern": "permission", "glob": "package.json"},
        ],
    )
    def test_zero_match_rescue_skipped_when_irrelevant(self, repowise_cwd, tool_input) -> None:
        """Regex patterns and single non-code-file scopes never rescue."""
        with patch.object(augment_cmd.search, "_fast_search_enrich") as enrich:
            result = _handle_search_post(
                tool_name="Grep",
                tool_input=tool_input,
                tool_output=GREP_FILES_MODE_ZERO,
                cwd=str(repowise_cwd),
            )
            assert not result
            enrich.assert_not_called()

    def test_triage_mode_on_flood(self, repowise_cwd) -> None:
        """>= _TRIAGE_THRESHOLD lines → triage mode."""
        from repowise.cli.commands.augment_cmd import _TRIAGE_THRESHOLD

        big = "\n".join(f"src/file{i}.py:1: hit" for i in range(_TRIAGE_THRESHOLD + 5))
        sentinel = object()
        with patch.object(
            augment_cmd.search, "_fast_search_enrich", return_value=sentinel
        ) as enrich:
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
        with patch.object(
            augment_cmd.search, "_fast_search_enrich", return_value=sentinel
        ) as enrich:
            out = _call("Grep", "auth", _flood(files=2, per_file=40), repowise_cwd)
            assert out == sentinel
            assert enrich.called

    def test_unparseable_flood_stays_silent(self, repowise_cwd) -> None:
        """No parseable files means no honest triage (plan item 8).

        This used to fall through to a triage that ranked index candidates
        with no reference to the grep output, which is precisely how it could
        name a file the search had not matched. With nothing to rank, the
        hook says nothing.
        """
        big = "\n".join(f"line {i} of something unstructured" for i in range(60))
        with patch.object(augment_cmd.search, "_fast_search_enrich") as enrich:
            assert _call("Grep", "auth", big, repowise_cwd) is None
            enrich.assert_not_called()

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


# ---------------------------------------------------------------------------
# Item 8/9/10: ranking over the grep's real results
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Returns canned row sets in call order; records the statements it saw."""

    def __init__(self, *row_sets):
        self._queue = list(row_sets)
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self._queue.pop(0) if self._queue else [])


class TestMatchedFiles:
    """What the hook believes the search actually returned."""

    def test_content_mode_gives_per_file_counts(self, repowise_cwd) -> None:
        text = "src/a.py:1:x\nsrc/a.py:9:x\nsrc/b.py:3:x"
        assert _matched_files(repowise_cwd, GREP_CONTENT_MODE, text) == {
            "src/a.py": 2,
            "src/b.py": 1,
        }

    def test_filenames_are_the_fallback_not_the_first_look(self, repowise_cwd) -> None:
        """Content mode carries ``filenames`` too; the counts must win."""
        payload = {"mode": "content", "filenames": ["src/z.py"], "content": ""}
        text = "src/a.py:1:x\nsrc/a.py:2:x"
        assert _matched_files(repowise_cwd, payload, text) == {"src/a.py": 2}

    def test_files_with_matches_uses_filenames(self, repowise_cwd) -> None:
        matched = _matched_files(repowise_cwd, GREP_FILES_MODE, "")
        assert matched == {
            "packages/web/src/lib/api/costs.ts": 1,
            "packages/web/src/app/repos/[id]/costs/page.tsx": 1,
        }

    def test_absolute_paths_become_node_ids(self, repowise_cwd) -> None:
        text = f"{repowise_cwd.as_posix()}/src/a.py:1:x"
        assert _matched_files(repowise_cwd, {}, text) == {"src/a.py": 1}

    def test_unreadable_output_is_none_not_empty(self, repowise_cwd) -> None:
        """None means "unknowable" and must not read as "nothing matched"."""
        assert _matched_files(repowise_cwd, {}, "some prose\nmore prose") is None


class TestPatternTerms:
    def test_splits_snake_and_camel(self) -> None:
        assert _pattern_terms("record_saving") == ["record", "saving"]
        assert _pattern_terms("recordSaving") == ["record", "saving"]

    def test_drops_short_tokens_and_dedups(self) -> None:
        assert _pattern_terms("db_id_handler_handler") == ["handler"]

    def test_empty_pattern_has_no_terms(self) -> None:
        assert _pattern_terms("()") == []


class TestCoverageOrder:
    def test_symbol_names_and_paths_both_count(self) -> None:
        order = _coverage_order(
            "record_saving",
            ["src/misc.py", "src/savings.py", "src/ledger.py"],
            {"src/ledger.py": ["record_saving"]},
        )
        # Full coverage from the symbol name beats half from the path, and
        # the file covering neither term is absent rather than ranked last.
        assert order == ["src/ledger.py", "src/savings.py"]

    def test_no_terms_gives_an_empty_leg(self) -> None:
        assert _coverage_order("[]", ["src/a.py"], {}) == []


class TestRRF:
    def test_agreement_across_legs_wins(self) -> None:
        scores = _rrf([["a", "b"], ["b", "a"], ["b"]])
        assert scores["b"] > scores["a"]

    def test_absent_from_every_leg_is_absent(self) -> None:
        assert "c" not in _rrf([["a"], ["b"]])


class TestTriageRanksRealMatches:
    async def test_only_matched_files_are_named(self) -> None:
        matched = {"src/a.py": 3, "src/b.py": 1}
        session = _FakeSession(
            # WikiSymbol rows: (file_path, name)
            [("src/b.py", "parse_yaml")],
            # GraphNode rows: (node_id, pagerank). The index also knows an
            # unmatched hub, which must never reach the output.
            [("src/a.py", 0.9), ("src/b.py", 0.1)],
        )
        out = await _triage(session, 1, "parse_yaml", "parse_yaml", 40, matched)
        assert out is not None
        named = [ln.strip().split()[0] for ln in out.splitlines()[1:]]
        assert set(named) <= set(matched)

    async def test_coverage_lifts_the_definition_over_the_hub(self) -> None:
        """Item 9: query-aware ranking beats bare PageRank.

        ``src/hub.py`` has more matches and far more centrality; ``src/b.py``
        is the only file whose symbol name covers the query. Two legs to one
        is what RRF is for.
        """
        matched = {"src/hub.py": 30, "src/b.py": 2}
        session = _FakeSession(
            [("src/b.py", "parse_yaml")],
            [("src/hub.py", 0.9), ("src/b.py", 0.01)],
        )
        out = await _triage(session, 1, "parse_yaml", "parse_yaml", 40, matched)
        assert out.splitlines()[1].strip().startswith("src/b.py")

    async def test_single_matched_file_is_not_worth_a_ranking(self) -> None:
        session = _FakeSession([("src/a.py", "x")], [("src/a.py", 0.5)])
        assert await _triage(session, 1, "x", "x", 40, {"src/a.py": 20}) is None

    async def test_silent_when_the_index_knows_none_of_them(self) -> None:
        session = _FakeSession([], [])
        matched = {"src/a.py": 3, "src/b.py": 1}
        assert await _triage(session, 1, "auth", "auth", 40, matched) is None

    async def test_header_states_the_population(self) -> None:
        matched = {"src/a.py": 3, "src/b.py": 1}
        session = _FakeSession([], [("src/a.py", 0.9), ("src/b.py", 0.1)])
        out = await _triage(session, 1, "auth", "auth", 40, matched)
        assert out.splitlines()[0] == (
            "[repowise] 40+ matches for `auth` across 2 files. "
            "Most likely relevant, ranked over the files your search matched:"
        )


class TestWidenedRescueGate:
    """Item 10: fire only on a file the grep did not return."""

    async def test_silent_when_the_symbol_is_in_a_matched_file(self) -> None:
        session = _FakeSession([("parse_yaml", "function", "src/a.py", 10)])
        out = await _rescue(
            session, None, 1, "parse_yaml", "parse_yaml", {"src/a.py": 3, "src/b.py": 1}
        )
        assert out is None

    async def test_fires_on_a_file_outside_the_result_set(self) -> None:
        session = _FakeSession([("parseYaml", "function", "src/other.py", 10)])
        out = await _rescue(
            session, None, 1, "parse_yaml", "parse_yaml", {"src/a.py": 3, "src/b.py": 1}
        )
        assert out == (
            "[repowise] `parse_yaml` matched 2 files, but not src/other.py:10, "
            "where indexed function `parseYaml` is defined."
        )

    async def test_no_fts_fallback_when_the_grep_found_things(self) -> None:
        """A wiki page suggestion is not new information against real hits."""
        session = _FakeSession([])
        out = await _rescue(session, None, 1, "parse_yaml", "parse_yaml", {"src/a.py": 3})
        assert out is None
