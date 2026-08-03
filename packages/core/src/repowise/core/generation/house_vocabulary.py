"""Selecting and rendering the repository's own vocabulary.

Two pages present mined house terms: the overview's capability table names the
six the repository is most about, and the glossary defines all of them. They are
the same computation at two depths — one ranked, corroborated list, sliced
short for the front page and long for the lookup page — so the selection lives
here and both callers read it rather than each deriving its own.

Everything in this module is deterministic. A term, the repository's own
sentence about it, and the path that sentence was read from are facts the run
already holds, and facts written by a model are resampled on every render: two
calls with the same prompt, the same model and the same temperature produced
overviews that disagreed on their row count and on which paths they cited. So
these tables are built here and embedded after the page comes back, and the
glossary is rendered without a model at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import structlog

from .concept_tree.vocabulary import HouseTerm, phrase_pattern, term_words

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SelectedTerm:
    """One mined term that earned a place on a page."""

    term: str
    #: The repository's own sentence, or ``None``. Never invented — a term the
    #: repository named without defining is still a term, and writing a
    #: definition for it is the one thing the vocabulary miner refuses to do.
    definition: str | None
    #: The document or source file the sentence was read from, falling back to
    #: the first document that named the term. Always a real path.
    source_path: str
    #: How many parts of the system name it. The strength of the corroboration
    #: rather than a fact about the repository, so it ranks and it logs; the
    #: overview does not render it.
    corroborating_pages: int
    #: Whether the codebase defines a symbol by this name. A term that is also
    #: a symbol may be rendered in backticks; a coined one may not, because the
    #: grounding pass strips backticks off any token it cannot resolve.
    is_indexed_symbol: bool = False
    #: What the structural side calls the parts of the system that name this
    #: term, in corpus order. This is the honest answer to "where is this used"
    #: — every entry is a module group cut from the dependency graph, so it
    #: points at code rather than at more prose.
    corroborating_names: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Is this prose a definition?
# ---------------------------------------------------------------------------
#
# A mined "definition" is whatever prose sat nearest the term, and near a term
# in a README that is often not a sentence about it. Two real examples from
# this repository's own front page:
#
#     CLI      -> "repowise init [PATH]      # index a codebase (one-time; ...)"
#     Distill  -> "`repowise distill <cmd>` compresses command output *before*
#                  the agent reads it:"
#
# The first is a line of shell with an inline comment. The second is a lead-in
# that ends on a colon because the explanation is the code block underneath.
# Neither says what the thing is, and an em dash is a better answer than
# either: the reader learns the term exists and is not misinformed about what
# it means.

#: A command line rather than a sentence: a shell comment, a prompt, an option
#: flag, a redirect or a pipeline.
#:
#: The redirect and pipe test requires whitespace on both sides. Bare ``<`` and
#: ``>`` reject real prose: "Decisions are co-located ... under the
#: ``decision:<record_id>`` namespace" is a sentence, and the angle brackets in
#: it are a placeholder, not a redirect.
_LOOKS_LIKE_COMMAND = re.compile(r"(^\s*[$>]\s|\s#\s|\s--?[a-zA-Z]|\s[|<>]\s)")
#: Ends where the real explanation begins — a colon, or an unclosed opener.
_TRAILS_OFF = (":", ",", ";", "-", "—", "–", "(", "[")
#: How a statement finishes. Checked as well as :data:`_TRAILS_OFF`, because
#: the two catch different things: that one rejects prose that stops mid-clause,
#: this one rejects text that was never prose. A stray line of source lifted out
#: of a scratch file — ``decisions/__init__.py ---- (PKG / "__init__.py")
#: .write_text( '`` — passes every other test here (it names the term, it has
#: words, it opens with a letter) and is stopped only by not ending in a way a
#: sentence can end. Every definition mined from four repositories that a human
#: would call a definition ends in one of these.
_SENTENCE_END = (".", "!", "?")


def is_a_sentence(text: str) -> bool:
    """Whether mined prose reads as a statement about the term.

    Deliberately shallow. This is not grammar checking — it is the difference
    between a sentence and a fragment of shell, and getting it wrong in the
    strict direction costs a definition, which every caller renders as an em
    dash. Getting it wrong the other way puts a command line on a page as
    though it explained something.
    """
    text = " ".join(text.split())
    # Three, not four: "Blast-radius request/response models." is terse and is
    # still the repository's own answer to what the term means. Two words is
    # where the fragments live ("See below", "Two parts").
    if len(text.split()) < 3:
        return False
    text = text.rstrip()
    if text.endswith(_TRAILS_OFF) or not text.endswith(_SENTENCE_END):
        return False
    if _LOOKS_LIKE_COMMAND.search(text):
        return False
    # A statement starts with a word, not with punctuation or a code fence.
    return text[:1].isalpha()


def cell(text: str) -> str:
    """Text safe to put in a markdown table cell.

    A pipe ends the cell wherever it appears, and mined prose is the
    repository's text rather than ours — a definition that quotes a shell
    pipeline or a reStructuredText grid row would otherwise shift every column
    to its right.
    """
    return " ".join(text.split()).replace("|", "\\|")


def clamp(text: str, limit: int) -> str:
    """*text* as one line and table-safe, cut to about *limit* characters.

    Truncates first, escapes second. The other order cuts an escaped ``\\|`` in
    half and leaves the backslash orphaned at the end of the cell, which is the
    one thing escaping exists to prevent. "About" because escaping runs after:
    a definition quoting a shell pipeline ends a character or two over, which
    costs nothing a reader can see.
    """
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return cell(text)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def select_terms(
    house_terms: Sequence[HouseTerm],
    module_names: Iterable[str],
    *,
    limit: int | None = None,
) -> list[SelectedTerm]:
    """The mined terms worth publishing, in the order they go on a page.

    Ranked by document frequency alone the mined terms are not publishable: on
    this repository the top of that list holds the repository's own name,
    "DONE", "Architecture" and "Files changed". So a term reaches a page only
    with corroboration from a second, independently-derived artifact.

    ``module_names`` are what the structural side calls the parts of the
    system: one string per module group, its title followed by its summary. A
    module group is cut from the dependency graph and named from the code, so a
    term appearing in one was arrived at twice — from the documents and from
    the structure — independently. That needs no stopword list and no
    per-repository tuning.

    Titles alone are about ninety short strings, which is too thin a net: it
    misses "Knowledge Graph" and "Code Health" while letting "Architecture" and
    "Workspace" through on an incidental word. The summaries are what make the
    corroboration mean something.

    Groups rather than written module *pages*, deliberately. A group exists on
    every run, so a scoped run that regenerates one page selects the same rows
    as a full one — a section that shrinks depending on how generation was
    invoked is the instability these deterministic tables exist to remove.

    **Multi-word terms come first.** Not as a filter — a single word still
    reaches the page when there is room — but ahead of single words, because a
    subsystem is nearly always named with two words ("blast radius", "dead
    code", "change risk") and an ordinary English word with one. The ranking in
    :func:`~...vocabulary.extract_house_terms` already encodes that as its
    tiebreak; here it leads, because document frequency barely discriminates
    (most repositories have two or three documents worth mining, so nearly
    every term ties at one) and a junk row is expensive on a short page.

    Ordering is total and derived only from the inputs, so two runs over an
    unchanged repository select the same terms in the same order. *limit*
    slices the tail: the whole list is one computation, and the front page
    takes its head while the glossary takes more of it.
    """
    corpus = [name for name in module_names if name]
    # Each entry is a group's title followed by its summary, so the first line
    # is the group's name — what to call the place a term turned up.
    titles = [entry.split("\n", 1)[0].strip() for entry in corpus]
    # The regex is the expensive part and nearly every (term, group) pair is a
    # miss, so a substring test on the term's first word rejects most pairs
    # before it runs. The same trick is what made the miner itself 3.3× faster.
    folded = [entry.lower() for entry in corpus]

    selected: list[SelectedTerm] = []
    for term in house_terms:
        words = term_words(term.term)
        if not words:
            continue
        # ASCII only. ``str.lower()`` and ``re.I`` disagree on a few code
        # points — "İ".lower() is two characters — so on a non-ASCII lead word
        # the cheap test can reject a pair the regex would have matched. Those
        # terms skip the prefilter and pay the regex.
        lead = words[0].lower() if words[0].isascii() else None
        pattern = phrase_pattern(term.term)
        # Sorted, not in corpus order. Everything else this function returns is
        # derived only from its inputs' *contents*, and a field that reordered
        # with the corpus would make two runs over an unchanged repository
        # disagree whenever module grouping shuffled.
        matched = tuple(
            sorted(
                titles[i]
                for i, entry in enumerate(corpus)
                if (lead is None or lead in folded[i]) and pattern.search(entry)
            )
        )
        hits = len(matched)
        if not hits:
            continue
        definition = term.definition
        # No "the definition must name the term" test. It was built, measured
        # and dropped: a heading gloss is the commonest definition shape there
        # is, and it does not restate its own heading. "## Blast radius" over
        # "The set of files a change can reach." is a repository defining its
        # term perfectly, and the rule deleted it — along with every definition
        # mined from a bolded lead-in, which captures only the text after the
        # dash. It caught two junk rows here and would have emptied the
        # definition column of any repository that writes that way.
        if definition and not is_a_sentence(definition):
            # Keep the row, drop the claim. The term is real — the structure
            # corroborated it — but the prose nearest it is not a statement
            # about it, and an em dash misinforms nobody.
            log.info(
                "house_vocabulary.definition_rejected",
                term=term.term,
                text=" ".join(definition.split())[:120],
            )
            definition = None
        # Cite where the sentence came from, or -- when there is no sentence,
        # including one just rejected -- the document that named the term.
        # Citing the home of prose the page declined to quote would point a
        # reader at a line that is not there.
        source = (term.definition_source if definition else None) or (
            term.source_paths[0] if term.source_paths else None
        )
        if source is None:
            # A term with no path at all cannot be cited, and an uncitable row
            # is the shape of claim this wiki does not make.
            continue
        selected.append(
            SelectedTerm(
                term=term.term,
                definition=definition,
                source_path=source,
                corroborating_pages=hits,
                is_indexed_symbol=term.is_indexed_symbol,
                corroborating_names=matched,
            )
        )

    selected.sort(key=lambda t: (len(term_words(t.term)) == 1, -t.corroborating_pages, t.term))
    kept = selected if limit is None else selected[:limit]
    log.info(
        "house_vocabulary.selected",
        mined=len(house_terms),
        corroborated=len(selected),
        kept=len(kept),
        terms=[t.term for t in kept[:12]],
    )
    if house_terms and not selected:
        # The documents name things the structure does not. That is a real
        # answer about a repository — marketing vocabulary with no cluster
        # behind it — but it is also what a corroboration corpus arriving empty
        # looks like, so the two counts that separate them are logged rather
        # than the section just not appearing.
        log.warning(
            "house_vocabulary.uncorroborated",
            mined=len(house_terms),
            module_names=len(corpus),
        )
    return kept
