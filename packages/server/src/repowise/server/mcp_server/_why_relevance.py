"""How relevant a decision record is to a question, and what to say when none is.

Split out of ``tool_why`` because these are the two halves of one judgement —
what to rank first, and when to serve nothing — and both are worth testing
without standing up a repo context.

The scoring problem this solves is specific. ``_score_decision`` adds a weight
per (field, word) hit, so a record is rewarded for saying an ordinary word in
many fields and for being long enough to contain many ordinary words. Measured
on this repo's 614 records: the question "why do we use ruff check instead of
ruff format" is answered by two ``active`` records titled "Never run ruff
format" and "Avoid formatting with ruff", and both lost to records matching
"use", "check", "format" and "instead" while containing no "ruff" at all. One of
the two answers is 48 characters long; under an occurrence count it cannot win
anything.
"""

from __future__ import annotations

import math
from typing import Any

from repowise.server.mcp_server._query_terms import content_terms

#: Weighted share of a question's vocabulary a record must carry to be served.
#:
#: Calibrated on a twenty-question set built before this constant existed, half
#: of it questions this store cannot answer, and swept rather than fitted. Every
#: value from 0.55 to 0.65 served the right record for all twelve answerable
#: questions; the first correct answer is suppressed at 0.70, and by 0.85 five
#: of the twelve are. Refusals move the other way — five of eight unanswerable
#: questions at 0.60, six at 0.65, seven at 0.75.
#:
#: This is the middle of the range where nothing correct is lost, rather than
#: its top. The top would score one refusal better and sit 0.03 from the lowest
#: real answer measured, which is a boundary fitted to one question rather than
#: a threshold. The two errors are not symmetric either: an over-served response
#: costs an agent one padded read, while a suppressed answer costs it the tool.
RELEVANCE_FLOOR = 0.6


def question_terms(query: str) -> list[str]:
    """Content words of *query*, singularised and deduplicated, longest first."""
    seen: dict[str, None] = {}
    for t in content_terms(query):
        seen.setdefault(stem(t), None)
    return list(seen)


def stem(term: str) -> str:
    """``comments`` -> ``comment``. A trailing plural and nothing more.

    Deliberately the crudest rule that fixes the case it was written for. The
    question "why must issue comments avoid em dashes" is answered by a record
    titled "Close issues and comment with short, simple text (no em dashes)",
    and it was refused because ``comments`` — the rarest word in the question,
    so the one carrying most of its weight — does not occur in a record that
    writes ``comment``.

    Applied to the question and to nothing else, which is why over-stripping is
    the safe direction and under-stripping is not: presence is a substring test,
    so a record writing ``comments`` still contains ``comment``, and ``status``
    reduced to ``statu`` still matches both ``status`` and ``statuses``. Words
    ending ``-ies`` are left whole for the same reason — turning ``queries``
    into ``query`` would stop it matching a record that writes ``queries``.

    Ceiling: English regular plurals. The upgrade path is a real stemmer, which
    is a dependency this earns only once misses appear that a trailing ``s``
    does not explain.
    """
    if term.endswith(("ies", "ss")):
        return term
    # ``-es`` only after the letters that require it: dashes -> dash, boxes ->
    # box, classes -> class. Stripping it unconditionally turned ``files`` into
    # ``fil``, which then matched ``filter`` and ``profile`` and made the
    # question's most ordinary word look like its rarest.
    if len(term) > 4 and term.endswith(("ches", "shes", "sses", "xes", "zes", "ses")):
        return term[:-2]
    if len(term) > 3 and term.endswith("s"):
        return term[:-1]
    return term


def term_idf(terms: list[str], corpus: list[str]) -> dict[str, float]:
    """``{term: weight}``, rarer terms weighing more, from *corpus* itself.

    A count of distinct terms present is the shape the sibling episode ranker
    established (see ``precedent.store.search``), and there BM25 supplies the
    rarity half. There is no BM25 here — the decision table is not an FTS index
    — so the weight is computed directly, over the records being ranked.

    This is not the document-frequency *ceiling* that was tried on session
    bodies and measured worse. That one dropped a term outright once it appeared
    in enough documents, which is a threshold fitted to a corpus's vocabulary.
    This is the continuous weight BM25 already applies, derived per call from
    the records in hand, and it fits no constant to anything.

    The expression is BM25's own, ``1 +`` included, and that guard is load
    bearing at the small end rather than the large one: plain ``log(N/df)``
    gives a term appearing in every record a weight of exactly zero, so on a
    store of three records all about one subsystem a question naming that
    subsystem would score its own answer at nothing and the tool would refuse
    itself. A gentler smoothing was tried first and compressed the range too
    far — on a seven-record store the word appearing once outweighed the word
    appearing seven times by only two to one, which was not enough for a terse
    record carrying the rare word to beat a long one carrying four ordinary
    ones.

    A word no record contains is weighted as if exactly one did. Absence is the
    strongest refusal signal there is — "vue", "candidacy" and "episode" are
    what make three of this store's unanswerable questions unanswerable — so
    dropping such words costs most of it: measured, refusals fell from five of
    eight to two of eight. But an unearnable weight left uncapped can put the
    floor out of reach of every record, and did: on a two-record store, "why is
    JWT used for authentication" scored the record titled "Use JWT for
    authentication" at 0.44, because "used" appears in neither record and, being
    unseen, outweighed both words that identify the answer. Capping at the
    rarest weight the store could actually award says what absence knows without
    claiming more than the corpus can support.
    """
    n = len(corpus)
    idf: dict[str, float] = {}
    for t in terms:
        df = sum(1 for text in corpus if t in text) or 1
        idf[t] = math.log(1 + (n - df + 0.5) / (df + 0.5))
    return idf


def relevance(text: str, idf: dict[str, float]) -> float:
    """Share of the question's *weight* that *text* carries, 0.0 to 1.0.

    Distinct terms only: a record repeating one word forty times is not more
    relevant than one answering the whole question, which is the failure the
    occurrence count had.
    """
    total = sum(idf.values())
    if total <= 0:
        return 0.0
    return sum(w for t, w in idf.items() if t in text) / total


def clears_floor(score: float) -> bool:
    """Whether a record is relevant enough to serve.

    One threshold and nothing beside it. A rescue for records carrying the
    question's rarest word alone was written first, for the case of a
    48-character record too terse to cover anything else — and then measured
    inert: presence is a substring test, so ``break`` is carried by
    "KV-cache-breaking" and the rescue admitted whatever the floor rejected,
    flat across every threshold from 0.55 to 0.85. Singularising the question
    (:func:`stem`) turned out to be what those cases actually needed.
    """
    return score >= RELEVANCE_FLOOR


# --- Redirect ---------------------------------------------------------------
#
# What to return when nothing clears the floor. The premise of the whole tool is
# that a long low-relevance answer is worse than no answer: an agent that reads
# three unrelated records concludes the tool is not worth calling and greps for
# the rest of the session. So this names the tool that *can* answer, and says in
# one sentence why this one did not.
#
# It fires only when the floor is missed. Stapling it to a good answer as a
# hedge trains the same distrust from the other direction.

_RISK_MARKERS = ("break", "impact of changing", "safe to change", "blast radius")
_CONTEXT_MARKERS = ("file", "module", "package", "class", "function", "directory")


def redirect_for(query: str) -> dict[str, Any]:
    """``{"try_instead": [...], "reason": ...}`` for a question with no record.

    Routed on the question's shape rather than its subject, because the subject
    is exactly what this store was just found to know nothing about.
    """
    q = query.lower().strip()

    if any(m in q for m in _RISK_MARKERS):
        tools = ["get_risk"]
    elif q.startswith(("what is", "what are", "what does", "what's")) and any(
        m in q for m in _CONTEXT_MARKERS
    ):
        tools = ["get_context"]
    elif q.startswith(("how ", "where ")):
        tools = ["get_answer", "search_codebase"]
    else:
        # A "why" with no record behind it is still a question about the code,
        # and get_answer reads the code rather than the decision store.
        tools = ["get_answer"]

    return {
        "try_instead": tools,
        "reason": (
            "No decision record covers this question. The store holds none "
            "carrying its terms, and the closest ones would be noise."
        ),
    }
