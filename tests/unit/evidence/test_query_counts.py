"""Query counts against a call site: grows, flat, or only ran."""

from __future__ import annotations

from repowise.core.evidence.query_counts import QueryReading, read_statement, strongest


def test_more_statements_at_more_items_grows() -> None:
    assert read_statement([(10, 11), (100, 101)]) == QueryReading("grows", 101, 100)


def test_the_same_count_at_two_sizes_is_flat() -> None:
    assert read_statement([(10, 2), (100, 2)]) == QueryReading("flat", 2, 100)


def test_one_size_or_no_size_only_ran() -> None:
    # Two scopes at one size, or scopes that never said their size, prove nothing.
    assert read_statement([(10, 11), (10, 12)]) == QueryReading("ran", 12, 10)
    assert read_statement([(None, 40), (None, 4)]) == QueryReading("ran", 40, None)


def test_growth_anywhere_outweighs_a_flat_pair() -> None:
    assert read_statement([(1, 2), (10, 2), (100, 50)]).verdict == "grows"


def test_flat_needs_every_size_to_agree() -> None:
    # A drop at the largest size is not a flat count.
    assert read_statement([(1, 3), (5, 3), (10, 2)]).verdict == "ran"


def test_the_strongest_reading_leads_and_none_reads_none() -> None:
    ran, flat = QueryReading("ran", 90, 9), QueryReading("flat", 1, 50)
    assert strongest([ran, flat]) == flat
    assert strongest([QueryReading("ran", 3, None), ran]) == ran
    assert strongest([]) is None
