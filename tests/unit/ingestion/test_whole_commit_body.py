"""A stored commit body cut at the byte ceiling is read back to its last whole sentence.

The indexer keeps at most ``_MAX_COMMIT_BODY_BYTES`` of a body, cut at a byte
count. The commit miners quote the body verbatim, so a decision mined from the
cut tail ended mid-word ("...could report the hooks a").
"""

from __future__ import annotations

import json

from repowise.core.analysis.decisions.commit_mining import pr_candidates, signal_commits
from repowise.core.ingestion.git_indexer._constants import (
    _MAX_COMMIT_BODY_BYTES,
    _truncate_body,
    body_was_cut,
    whole_body,
)

#: Enough wrapped prose to fill most of the ceiling, ending in a full stop.
_LEAD = (
    "Doctor gains a row to replace a guess. A hook whose command cannot start is a silent\n"
    "no-op, so the status could report the hooks as installed while they\n"
    "never ran.\n\n"
) * 4


def _cut(text: str) -> str:
    """*text* padded past the ceiling and cut there, as the indexer stores it."""
    stored = _truncate_body(text + "x" * _MAX_COMMIT_BODY_BYTES)
    assert body_was_cut(stored)
    return stored


def _filled(lead: str, tail: str) -> str:
    """*lead* then *tail* repeated so the ceiling falls inside the last *tail*."""
    room = _MAX_COMMIT_BODY_BYTES - len(lead.encode())
    return lead + tail * (room // len(tail) + 2)


def test_a_body_under_the_ceiling_is_unchanged():
    body = "Switch to X.\nThe old path\nwrapped here without a stop"
    assert whole_body(body) == body
    assert whole_body("") == ""


def test_a_cut_wrapped_paragraph_ends_at_its_last_sentence_not_at_a_wrap():
    stored = _cut(_filled(_LEAD, "The status line\nreads the ledger and\nwarns when it is stale. "))
    kept = whole_body(stored)
    assert stored.startswith(kept)
    assert kept.endswith("warns when it is stale.")


def test_a_cut_bullet_list_keeps_whole_items_and_never_joins_two():
    stored = _cut(_filled(_LEAD, "- drop the old reader\n  so the cache stays warm\n"))
    kept = whole_body(stored)
    assert stored.startswith(kept)
    assert kept.endswith("- drop the old reader\n  so the cache stays warm")


def test_a_cut_inside_trailers_falls_back_to_the_paragraph_before():
    body = _filled(_LEAD + "Use the shared helper.\n\n", "Co-authored-by: Someone <a@b.c>\n")
    # The trailers carry no full stop and no bullet, so the blank line is the break.
    assert whole_body(_cut(body)) == (_LEAD + "Use the shared helper.").rstrip()


def test_crlf_bodies_break_at_a_blank_line_and_never_at_a_wrap():
    lead = _LEAD.replace("\n", "\r\n") + "Heading without a stop\r\n\r\n"
    stored = _cut(_filled(lead, "a wrapped\r\nclause without a stop "))
    assert whole_body(stored) == lead.rstrip()


def test_a_list_number_or_abbreviation_is_not_a_sentence_end():
    lead = _LEAD + "Steps\n\n"
    stored = _cut(_filled(lead + "1. ", "read it, e.g. the ledger and "))
    assert whole_body(stored) == lead.rstrip()


def test_a_sentence_ending_in_a_number_is_a_sentence_end():
    lead = _LEAD + "Bumps the action from 2 to 3. "
    stored = _cut(_filled(lead, "a clause without a stop "))
    assert whole_body(stored) == lead.rstrip()


def test_a_body_one_split_character_short_of_the_ceiling_still_reads_as_cut():
    stored = _truncate_body("a" * (_MAX_COMMIT_BODY_BYTES - 3) + "😀")
    assert len(stored.encode()) == _MAX_COMMIT_BODY_BYTES - 3
    assert body_was_cut(stored)
    whole = "a" * (_MAX_COMMIT_BODY_BYTES - 4)
    assert not body_was_cut(whole)
    assert whole_body(whole) == whole


def test_a_filename_at_the_cut_is_not_read_as_a_sentence_end():
    filler = _filled(_LEAD, "and more ")[: _MAX_COMMIT_BODY_BYTES - len("registry.")]
    stored = _truncate_body(filler + "registry.py")
    assert stored.endswith("registry.")
    assert whole_body(stored) == _LEAD.rstrip()


def test_a_cut_body_with_no_break_is_left_as_is():
    stored = _truncate_body("word " * _MAX_COMMIT_BODY_BYTES)
    assert whole_body(stored) == stored


def _meta(body: str) -> dict:
    commit = {
        "sha": "abc12345",
        "message": "perf(hooks): replace the hook launcher",
        "body": body,
        "author": "a",
        "date": "2026-01-01",
        "pr_number": 7,
    }
    return {"a.py": {"significant_commits_json": json.dumps([commit])}}


def test_both_miners_read_the_whole_sentence_body():
    stored = _cut(_LEAD + "Doctor and\nhook status could report the hooks a")
    commits, _ = signal_commits(_meta(stored))
    candidates, _ = pr_candidates(_meta(stored))
    expected = _LEAD.rstrip()
    assert commits["abc12345"]["body"] == expected
    assert candidates["abc12345"]["body"] == expected
