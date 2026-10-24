"""Identifier words: a name split into its words, and words joined in a
language's casing.
"""

from __future__ import annotations

from repowise.core.analysis.health.refactoring.naming import join_identifier, split_words

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
