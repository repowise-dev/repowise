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
from .declared_glossary import DeclaredTerm, declared_avoid_index, phrase_key

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SelectedTerm:
    """One term that earned a place on a page.

    Mined by default. A term from a glossary the team authored carries
    ``status="declared"`` instead, and is the authority on how the thing is
    named: the whole point of the declared half is that a person wrote it down.
    """

    term: str
    #: The repository's own sentence, or ``None``. Never invented — a term the
    #: repository named without defining is still a term, and writing a
    #: definition for it is the one thing the vocabulary miner refuses to do.
    #: For a declared term this is the team's own sentence instead, quoted for
    #: the same reason and never paraphrased.
    definition: str | None
    #: The document or source file the sentence was read from, falling back to
    #: the first document that named the term. Always a real path.
    source_path: str
    #: How many parts of the system name it. The strength of the corroboration
    #: rather than a fact about the repository, so it ranks and it logs; the
    #: overview does not render it. Zero on a declared term that is not in the
    #: code yet, which is the signal rather than a defect: a word the team
    #: decided on before the code caught up.
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
    #: The nearest authoritative prose retained for a later synthesis pass even
    #: when it is not semantically adequate to publish as the definition.
    definition_evidence: str | None = None
    definition_evidence_source: str | None = None
    #: ``"declared"`` when a person wrote this term down as canonical, else
    #: ``"mined"``. The distinction is carried onto the page rather than
    #: inferred from which list a row came out of.
    status: str = "mined"
    #: The synonyms the team marked wrong for this concept, as written.
    avoid: tuple[str, ...] = ()
    #: The bounded context the term belongs to, or ``None`` for a repository
    #: that declares one vocabulary for the whole tree.
    context: str | None = None
    #: Set on a mined term whose phrase the declared glossary avoids: the
    #: canonical term to use instead. Demoted, never deleted — it is evidence
    #: the code says one thing and the team another.
    demoted_by: str | None = None


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
_DEFINITION_VERBS = frozenset(
    {
        "are",
        "builds",
        "captures",
        "combines",
        "compresses",
        "contains",
        "describes",
        "enables",
        "finds",
        "handles",
        "holds",
        "identifies",
        "is",
        "means",
        "manages",
        "measures",
        "orchestrates",
        "proposes",
        "provides",
        "records",
        "refers",
        "represents",
        "scores",
        "stores",
        "tracks",
        "uses",
        "walks",
    }
)
_HEADING_GLOSS_NOUNS = frozenset(
    {
        "ability",
        "analog",
        "boundary",
        "collection",
        "file",
        "graph",
        "index",
        "layer",
        "measure",
        "method",
        "model",
        "process",
        "record",
        "relationship",
        "representation",
        "score",
        "set",
        "signal",
        "store",
        "system",
        "view",
        "way",
    }
)


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


def is_definition(term: str, text: str) -> bool:
    """Whether nearby prose actually defines *term*, not merely mentions it.

    Sentence shape and term corroboration answer different questions. This
    conservative semantic gate accepts an explicit term-led description or a
    conventional heading gloss ("The set...", "A process..."). Ambiguous prose
    stays available as evidence for a later bounded synthesis pass but is not
    published verbatim as a definition.
    """
    if not is_a_sentence(text):
        return False
    normalized = " ".join(text.split())
    words = re.findall(r"[A-Za-z0-9]+", normalized.lower())
    # Keep the term's literal inflection here. ``term_words`` is intentionally
    # stemmed for matching/ranking, but a definition headed "Decisions are"
    # must compare with the plural heading rather than the stem "decision".
    term_tokens = re.findall(r"[A-Za-z0-9]+", term.lower())
    if term_tokens and words[: len(term_tokens)] == term_tokens:
        next_index = len(term_tokens)
        if next_index >= len(words):
            return False
        if words[next_index] in _DEFINITION_VERBS or words[next_index] == "for":
            return True
        # Repositories commonly use a terse noun gloss under a term heading,
        # e.g. "Blast-radius request/response models." Require the phrase to
        # end in a known definitional noun; length alone admits incidental
        # claims such as "Dead code fails often."
        final_word = words[-1]
        singular_final = final_word[:-1] if final_word.endswith("s") else final_word
        return singular_final in _HEADING_GLOSS_NOUNS
    if len(words) >= 2 and words[0] in {"a", "an", "the", "this"}:
        return words[1] in _HEADING_GLOSS_NOUNS
    return False


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


def _corroborated(
    term: str,
    *,
    titles: Sequence[str],
    corpus: Sequence[str],
    folded: Sequence[str],
) -> tuple[str, ...]:
    """The module groups whose titles/summaries name *term*, sorted."""
    # Sorted, not in corpus order. Everything else this function returns is
    # derived only from its inputs' *contents*, and a field that reordered
    # with the corpus would make two runs over an unchanged repository
    # disagree whenever module grouping shuffled.
    words = term_words(term)
    if not words:
        return ()
    # ASCII only. ``str.lower()`` and ``re.I`` disagree on a few code points —
    # "İ".lower() is two characters — so on a non-ASCII lead word the cheap
    # test can reject a pair the regex would have matched. Those terms skip
    # the prefilter and pay the regex.
    lead = words[0].lower() if words[0].isascii() else None
    pattern = phrase_pattern(term)
    return tuple(
        sorted(
            titles[i]
            for i, entry in enumerate(corpus)
            if (lead is None or lead in folded[i]) and pattern.search(entry)
        )
    )


def corroboration_corpus(module_names: Iterable[str]) -> tuple[list[str], list[str], list[str]]:
    """``(titles, corpus, folded)`` — the pre-computed form the helpers take.

    Three lists rather than one because the prefilter and the regex read
    different spellings of the same corpus, and recomputing them per term is
    the expensive part of a selection that runs over every term on every page.
    """
    corpus = [name for name in module_names if name]
    # Each entry is a group's title followed by its summary, so the first line
    # is the group's name — what to call the place a term turned up.
    titles = [entry.split("\n", 1)[0].strip() for entry in corpus]
    # The regex is the expensive part and nearly every (term, group) pair is a
    # miss, so a substring test on the term's first word rejects most pairs
    # before it runs. The same trick is what made the miner itself 3.3× faster.
    folded = [entry.lower() for entry in corpus]
    return titles, corpus, folded


def select_declared_terms(
    declared: Sequence[DeclaredTerm],
    module_names: Iterable[str],
    house_terms: Sequence[HouseTerm] = (),
) -> list[SelectedTerm]:
    """Every term the team declared, each with whatever corroboration it has.

    The carve-out the DDD framing asks for, and the one rule that separates
    this from the mined path: **a declared term is never dropped for lack of
    corroboration.** ``select_terms`` gates on the structure — bind-or-drop is
    right for vocabulary a document invented and the code never confirmed — but
    a word a person wrote down and the code has not caught up to yet is a
    signal, not noise. It reaches the page carrying ``corroborating_pages == 0``
    and renders as "not yet in code".

    Corroboration is still computed, because "where is this used" is a column a
    reader acts on and a declared term that *is* in the code should say where.

    The definition is the team's own sentence, quoted. It is not put through
    :func:`is_definition`: that test exists to separate a sentence about a term
    from the shell fragment nearest to it in mined prose, and applying it to a
    `CONTEXT.md` would delete perfectly good one-line definitions for being
    terse. The author is the authority on what their sentence means.
    """
    titles, corpus, folded = corroboration_corpus(module_names)
    #: Mined terms, by phrase, so a declared term can take the symbol flag the
    #: miner already worked out for the same phrase.
    mined_by_key: dict[str, HouseTerm] = {}
    for term in house_terms:
        mined_by_key.setdefault(phrase_key(term.term), term)

    selected: list[SelectedTerm] = []
    for declared_term in declared:
        corroboration = _corroborated(
            declared_term.term, titles=titles, corpus=corpus, folded=folded
        )
        mined = mined_by_key.get(phrase_key(declared_term.term))
        selected.append(
            SelectedTerm(
                term=declared_term.term,
                definition=declared_term.definition,
                # Always a real path: the glossary file itself. A declared term
                # needs no document to have named it — the team named it.
                source_path=declared_term.source_path,
                corroborating_pages=len(corroboration),
                is_indexed_symbol=bool(mined and mined.is_indexed_symbol),
                corroborating_names=corroboration,
                # No evidence fallback: the definition is the team's sentence,
                # and there is nothing the miner found that should stand behind
                # or beside it.
                definition_evidence=None,
                definition_evidence_source=None,
                status="declared",
                avoid=declared_term.avoid,
                context=declared_term.context,
            )
        )
    if selected:
        log.info(
            "house_vocabulary.declared_selected",
            declared=len(declared),
            kept=len(selected),
            sources=sorted({t.source_path for t in selected}),
            terms=[t.term for t in selected[:12]],
        )
    return selected


def select_terms(
    house_terms: Sequence[HouseTerm],
    module_names: Iterable[str],
    *,
    limit: int | None = None,
    declared: Sequence[DeclaredTerm] = (),
) -> list[SelectedTerm]:
    """The terms worth publishing, in the order they go on a page.

    Three rules, in order of who is the authority:

    1. **A declared term is the canonical word.** It leads the page, it is
       never dropped for lack of corroboration (see
       :func:`select_declared_terms`), and a mined term spelling the same
       phrase is folded into it rather than listed a second time under a
       different status.
    2. **A mined term whose phrase the glossary marks ``_Avoid_`` is demoted.**
       Kept, not deleted, and ranked last with ``demoted_by`` naming the word
       to use instead: the row is evidence that the code says one thing and the
       team agreed on another, and silently dropping it is how a glossary
       becomes something nobody can audit.
    3. **Everything else is mined**, selected by structural corroboration
       exactly as before.

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

    declared_terms = select_declared_terms(declared, module_names, house_terms)
    declared_keys = {phrase_key(term.term) for term in declared_terms}
    avoided = declared_avoid_index(declared)
    selected: list[SelectedTerm] = []
    demoted: list[SelectedTerm] = []
    for term in house_terms:
        key = phrase_key(term.term)
        if not key:
            continue
        if key in declared_keys:
            # The same phrase, already on the page as the canonical word. One
            # row per concept: listing both would present a choice the team
            # already made.
            continue
        matched = _corroborated(term.term, titles=titles, corpus=corpus, folded=folded)
        hits = len(matched)
        if not hits:
            continue
        definition_evidence = term.definition
        definition = definition_evidence
        # No "the definition must name the term" test. It was built, measured
        # and dropped: a heading gloss is the commonest definition shape there
        # is, and it does not restate its own heading. "## Blast radius" over
        # "The set of files a change can reach." is a repository defining its
        # term perfectly, and the rule deleted it — along with every definition
        # mined from a bolded lead-in, which captures only the text after the
        # dash. It caught two junk rows here and would have emptied the
        # definition column of any repository that writes that way.
        if definition and not is_definition(term.term, definition):
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
        row = SelectedTerm(
            term=term.term,
            definition=definition,
            source_path=source,
            corroborating_pages=hits,
            is_indexed_symbol=term.is_indexed_symbol,
            corroborating_names=matched,
            definition_evidence=definition_evidence,
            definition_evidence_source=(
                term.definition_source if definition_evidence else None
            ),
            demoted_by=avoided.get(key),
        )
        (demoted if row.demoted_by else selected).append(row)

    selected.sort(key=lambda t: (len(term_words(t.term)) == 1, -t.corroborating_pages, t.term))
    demoted.sort(
        key=lambda t: (len(term_words(t.term)) == 1, -t.corroborating_pages, t.term)
    )
    if demoted:
        log.info(
            "house_vocabulary.avoided_term_demoted",
            count=len(demoted),
            terms=[t.term for t in demoted[:12]],
            canonical=sorted({t.demoted_by or "" for t in demoted}),
        )
    # Declared first, in the order the team wrote them; then the mined terms
    # the structure confirmed; then whatever the glossary marks wrong. The
    # order is the whole ranking rule, and it is total.
    ranked = declared_terms + selected + demoted
    kept = ranked if limit is None else ranked[:limit]
    log.info(
        "house_vocabulary.selected",
        mined=len(house_terms),
        declared=len(declared_terms),
        corroborated=len(selected),
        kept=len(kept),
        terms=[t.term for t in kept[:12]],
    )
    if house_terms and not selected and not declared_terms:
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

