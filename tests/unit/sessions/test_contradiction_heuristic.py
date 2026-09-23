"""`contradicts()` needs two-sided evidence, not one text's mood.

The heuristic gates on shared content tokens and then asks whether the two
texts push in opposite directions. It used to accept a directional reversal
signal found in *either* text on its own, which says that text changes
something and never that it changes *this*. Token overlap cannot supply the
missing half: measured over this repository's whole injection corpus, 426
(decision, quote) pairs behind 201 judged rows, 96% of pairs share zero or one
token and the two-sided test fires on none of them, while the lone-reversal
branch fires exactly once — and wrongly. That single firing is the whole of
the 10.0% contradiction rate the layer has published.

That measurement was taken against the straddle as it matched then, by
unanchored substring. The tests below also pin the anchoring that followed, in
both directions: the false positives it removes, the inflections it must not
cost, and one false positive it cannot reach. The straddle has its own
false-positive rate and this corpus has not measured it.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.decisions.evolution import contradicts

#: The exact pair behind all 20 contradicted rows on this repository's store.
#: They share precisely two content tokens, *run* and *not*, which clears the
#: shared-topic gate, and the quote contains the reversal signal *migration*.
_RUFF_RECORD = "Never run Ruff format. Do not run `ruff format` on the codebase."
_PRODUCTION_QUOTE = (
    "Do not run migration 141, backfill, update rows, deploy, alter Dodo, "
    "or otherwise mutate production."
)


def test_a_lone_reversal_word_is_not_a_contradiction():
    """The false positive that produced this layer's only outcome number."""
    assert contradicts(_RUFF_RECORD, _PRODUCTION_QUOTE) == (False, "")


def test_a_caller_with_its_own_topic_gate_can_opt_the_branch_back_in():
    """Supersession reaches the heuristic only for vector-similar pairs.

    There "the newer one says it replaces something" is genuine evidence,
    because something stronger than token overlap already established the two
    are about the same thing.
    """
    assert contradicts(_RUFF_RECORD, _PRODUCTION_QUOTE, lone_reversal_counts=True) == (
        True,
        "migration",
    )


def test_an_opposing_verb_pair_straddling_the_texts_still_fires():
    """The two-sided test is the default's whole content."""
    fired, signal = contradicts(
        "Use JWT tokens for service auth. all service auth uses JWT tokens",
        "no, stop using JWT tokens for service auth, revert to sessions",
    )
    assert (fired, signal) == (True, "opposing-verbs")


def test_opposing_verbs_still_need_a_shared_topic():
    """Otherwise "adopt X" contradicts "drop Y" for unrelated X and Y."""
    assert contradicts(
        "Adopt Postgres for the primary store.",
        "we removed the legacy avatar cropper",
    ) == (False, "")


def test_an_unrelated_correction_does_not_contradict():
    assert contradicts(
        "Use JWT tokens for service auth. all service auth uses JWT tokens",
        "no, format the changelog with bullet points please",
    ) == (False, "")


def test_an_opposing_verb_must_be_a_word_and_not_a_substring():
    """`use` lives inside *user*, *because* and *reuse*; `add` inside *address*.

    Unanchored membership made the straddle fire on a correction that agrees
    with its record: `use` matched inside *user*, against `remove` in the
    quote. It matters more now than it did, because with the lone-reversal
    branch gone the straddle is the only firing path the default has.
    """
    assert contradicts(
        "Never log user email addresses. Do not put a user email in any log line.",
        "no, do not remove the user email validation from the signup form",
    ) == (False, "")


def test_opposing_verbs_about_different_objects_still_fire():
    """A known limit of the straddle, recorded rather than claimed fixed.

    Here `use` and `dropped` are both whole words and both really present, so
    word-boundary matching does not and cannot help: the two verbs are about
    different objects, and nothing short of knowing what each verb governs
    separates this from a real reversal. The pair is only reached at all
    because `mcp` and `context` are shared, and it is why the default path
    remains a cheap pre-filter rather than a judgement.
    """
    assert contradicts(
        "Use the repowise MCP for context. Always use the repowise mcp tools.",
        "no, we dropped the legacy mcp context shim because it was unused",
    ) == (True, "opposing-verbs")


@pytest.mark.parametrize(
    ("record", "quote"),
    [
        ("Uses Postgres for the primary store",
         "no, we removed Postgres from the primary store"),
        ("Used Postgres for the primary store",
         "no, we removed Postgres from the primary store"),
        ("Adopted Postgres for the primary store",
         "no, that removes Postgres from the primary store"),
        ("Adds Postgres to the primary store",
         "no, we dropped Postgres from the primary store"),
        ("Use Postgres for the primary store",
         "no, we abandoned Postgres for the primary store"),
        ("Enable the Postgres pool", "no, that disables the Postgres pool"),
        ("Adopt the Postgres pool", "no, it reverts the Postgres pool"),
        ("Introduced the Postgres pool", "no, we removed the Postgres pool"),
        # `-ing` the suffix group cannot build: silent-e and doubled consonant.
        ("Adopt the Postgres pool", "no, we are removing the Postgres pool"),
        ("Adopt the Postgres pool", "no, we are dropping the Postgres pool"),
        ("Adopt the Postgres pool", "no, we are disabling the Postgres pool"),
        ("Enabling the Postgres pool", "no, that disables the Postgres pool"),
    ],
)
def test_the_straddle_survives_the_inflections_corrections_are_written_in(record, quote):
    """Anchoring the verbs must not cost the forms people actually write.

    `_OPPOSING_VERB_PAIRS` lists `removed` but not *removes*, `use` but not
    *uses*. Plain whole-word matching dropped every one of these, which on the
    injection side is the only firing path there is, so anchoring alone would
    have cost more recall than the false-positive class it removes. The `-ing`
    forms the suffix group cannot build are listed in the pairs instead.
    """
    assert contradicts(record, quote) == (True, "opposing-verbs")


def test_one_hyphenated_word_cannot_satisfy_both_sides_of_a_pair():
    """`blocking` sits inside *non-blocking*, and a hyphen is a word boundary.

    So whole-word matching alone does not separate them: a text containing
    only *non-blocking* matched the sync side and the async side at once, and
    two texts that agree scored as a contradiction. The left edge excludes the
    hyphen for this. Same class, reached by substring rather than by boundary:
    `sync` inside *asynchronous*.
    """
    assert contradicts(
        "Use non-blocking IO in the request handler",
        "no, keep the non-blocking IO request handler",
    ) == (False, "")
    assert contradicts(
        "The IO layer is asynchronous everywhere",
        "no, keep the IO layer asynchronous everywhere",
    ) == (False, "")
