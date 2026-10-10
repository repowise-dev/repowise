"""The shared identifier slug both name-producing detectors run through.

It exists so ``suggested_name`` means one thing across the payload rather than
one thing per detector.
"""

from __future__ import annotations

from repowise.core.analysis.health.refactoring.naming import (
    banner_words,
    identifier_slug,
    join_identifier,
    label_words,
    split_words,
)


def test_lowercases_and_keeps_alphanumerics():
    assert identifier_slug("Providers") == "providers"
    assert identifier_slug("http2") == "http2"


def test_collapses_non_alphanumeric_runs_to_single_underscores():
    assert identifier_slug("api-client") == "api_client"
    assert identifier_slug("a..--..b") == "a_b"
    assert identifier_slug("pkg/sub") == "pkg_sub"


def test_strips_leading_and_trailing_separators():
    assert identifier_slug("--api--") == "api"
    assert identifier_slug("_private_") == "private"


def test_leading_digit_is_made_identifier_safe():
    # Not a valid identifier start in most languages.
    assert identifier_slug("3d") == "_3d"
    assert identifier_slug("2fa-token") == "_2fa_token"


def test_unusable_input_returns_empty_so_callers_can_pick_a_fallback():
    assert identifier_slug("") == ""
    assert identifier_slug(None) == ""
    assert identifier_slug("---") == ""
    assert identifier_slug("...") == ""


# -- word splitter --------------------------------------------------------------


def test_split_words_separates_every_casing_of_one_name():
    # The three spellings a reader uses for the same idea must give one word
    # list, or a name's shape would depend on how its author capitalised it.
    assert split_words("meanValue") == ["mean", "value"]
    assert split_words("mean_value") == ["mean", "value"]
    assert split_words("MeanValue") == ["mean", "value"]
    assert split_words("mean-value") == ["mean", "value"]
    assert split_words("mean.value") == ["mean", "value"]


def test_split_words_keeps_a_capital_run_as_one_word():
    # Splitting a run of capitals needs a dictionary (is ``HTTPServer``
    # ``HTTP``+``Server`` or ``HTTP``+``server``?), which this module does not
    # have; one word is the honest answer.
    assert split_words("HTTPStatus") == ["httpstatus"]
    assert split_words("IDs") == ["ids"]


def test_split_words_drops_separators_and_digit_boundaries():
    assert split_words("__a__b__") == ["a", "b"]
    assert split_words("api2client") == ["api2client"]
    assert split_words("2fa") == ["2fa"]
    # A digit-to-capital boundary splits, the same as lower-to-capital.
    assert split_words("file2Name") == ["file2", "name"]


def test_split_words_keeps_non_ascii_letters():
    # An ASCII-only character class would drop the accented letters and the
    # remaining word would silently lose its meaning.
    assert split_words("café") == ["café"]
    assert split_words("naïveValue") == ["naïve", "value"]
    assert split_words("método") == ["método"]


def test_split_words_of_unusable_input_is_empty():
    assert split_words("") == []
    assert split_words(None) == []
    assert split_words("___") == []


# -- joiner ---------------------------------------------------------------------


def test_join_identifier_renders_each_convention():
    words = ["compute", "mean", "value"]
    assert join_identifier(words, "snake_case") == "compute_mean_value"
    assert join_identifier(words, "camelCase") == "computeMeanValue"


def test_join_identifier_uses_the_first_word_as_lowercase_head():
    # The head is not capitalised: a lifted helper is a local, and a leading
    # capital would make it a type name in Go and Java.
    assert join_identifier(["compute", "average"], "camelCase") == "computeAverage"


def test_join_identifier_of_no_words_is_empty():
    assert join_identifier([], "camelCase") == ""
    assert join_identifier([], "snake_case") == ""


# -- banner comments and stage labels ---------------------------------------------


def test_banner_words_reads_one_short_line_between_rules():
    assert banner_words(["# Build table"]) == ["build", "table"]
    assert banner_words(["// Group by consumer repo"]) == ["group", "by", "consumer", "repo"]
    assert banner_words(["# ---- Graph metrics ----------"]) == ["graph", "metrics"]
    assert banner_words(["# ── Core runtime state ──"]) == ["core", "runtime", "state"]
    assert banner_words(["# =====", "# Load edges", "# ====="]) == ["load", "edges"]
    assert banner_words(["/* Load edges */"]) == ["load", "edges"]
    # Step numbers, asides and articles are not part of the name.
    assert banner_words(["# 3. Remove the wrapper script"]) == ["remove", "wrapper", "script"]
    assert banner_words(["# Step 1: Parse git logs"]) == ["parse", "git", "logs"]
    assert banner_words(["# Neighboring communities (cap at 5)"]) == ["neighboring", "communities"]


def test_banner_words_refuses_prose_code_and_directives():
    assert banner_words([]) == []
    assert banner_words(["# Decisions"]) == []  # a heading, not a name
    assert banner_words(["# Lazy import to avoid circular dependency at load"]) == []
    assert banner_words(["# Sum values", "# then count them"]) == []
    assert banner_words(["# Build labels and map indices -> skill names"]) == []
    assert banner_words(["# Check for image/picture"]) == []
    assert banner_words(["# [3] get_running_pid()"]) == []
    assert banner_words(["# Kimi: top-level reasoning effort"]) == []
    assert banner_words(["// eslint-disable-next-line no-await-in-loop"]) == []
    assert banner_words(["# TODO tidy this up"]) == []


def test_label_words_reads_a_timed_stage_label():
    assert label_words('with timed(timings, "persist.pages"):') == ["persist", "pages"]
    assert label_words("with timed(self._t, 'detect_changes'):\n    x = 1") == ["detect", "changes"]
    assert label_words('with span("persist.pages"):') == []
    assert label_words("x = timed") == []
