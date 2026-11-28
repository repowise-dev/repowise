"""Fold decisions mined twice from one commit into the record already holding it.

Two folds, both keyed on a shared evidence commit:

* across lanes, a ``git_archaeology`` decision takes the title of the ``pr``
  decision mined from the same merge (:func:`fold_archaeology_into_pr`);
* within a lane, a re-mined PR or commit body that rewords the title and moves
  the span folds into the stored record saying the same thing
  (:class:`RewordingIndex`). Re-mining runs on every update, and the span
  decides the files and quote the id derives from, so without this each pass
  minted a record for the decision the last pass already stored.

The commit alone is never enough, because one PR states several distinct
decisions: the rewording fold also needs the decision text to match (one text
opens the other, or their content words overlap almost entirely), or the
titles to name the same choice (most of their content words shared). A re-mine
often restates the decision from another part of the body while keeping the
title close, so the title catches what the text rule misses.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable

from repowise.core.generation.page_overlap import jaccard

from ..models import DecisionRecord
from .decision_evidence import _first_commit

#: Marks an incoming decision this fold retitled, so its files join the PR record's.
FOLDED_INTO_PR = "_folded_into_pr"

# Miners may echo a short sha, so shas compare by prefix of at least git's default length.
_MIN_SHA_PREFIX = 7

# A merge with several PR decisions pairs by title overlap. Fixed and strict
# because no labelled pairs exist to tune it; a looser floor needs them.
_MIN_TITLE_OVERLAP = 0.5


def fold_archaeology_into_pr(
    decisions: list[dict],
    existing: Iterable[DecisionRecord],
    normalize: Callable[[str], str],
) -> list[dict]:
    """Retitle each ``git_archaeology`` decision that restates a ``pr`` decision of its commit.

    The PR lane mines a merge or squash commit's body and the archaeology lane
    mines the same commit, so one merge can yield one decision under two
    titles. Taking the PR title lets title dedup fold the pair into the PR
    record, which outranks the commit lane and so keeps its id and wording.

    A commit with one PR decision pairs with it; with several, the unique best
    title overlap above :data:`_MIN_TITLE_OVERLAP` does. A PR title that two
    archaeology decisions pair with folds neither, since the pairing is then
    ambiguous. A pairing onto a dismissed PR record is dropped with it, as a
    restatement of a dismissed decision.
    """
    pr_titles = _pr_titles_by_commit(decisions, existing, normalize)
    if not pr_titles:
        return decisions

    pairs = {
        i: _pair(d, pr_titles, normalize)
        for i, d in enumerate(decisions)
        if d.get("source") == "git_archaeology"
    }
    claims = Counter(filter(None, pairs.values()))
    out: list[dict] = []
    for i, d in enumerate(decisions):
        pair = pairs.get(i)
        if pair is not None and claims[pair] == 1:
            d = {**d, "title": pair[2], FOLDED_INTO_PR: True}
        out.append(d)
    return out


def _pr_titles_by_commit(
    decisions: list[dict],
    existing: Iterable[DecisionRecord],
    normalize: Callable[[str], str],
) -> dict[str, dict[str, str]]:
    """Commit sha -> the PR titles mined from it (normalized -> original), incoming and stored."""
    index: dict[str, dict[str, str]] = {}
    for d in decisions:
        if d.get("source") == "pr":
            _add(index, _first_commit(d), d.get("title", ""), normalize)
    for rec in existing:
        if rec.source == "pr":
            _add(index, _record_commit(rec), rec.title, normalize)
    return index


def _pair(
    d: dict, pr_titles: dict[str, dict[str, str]], normalize: Callable[[str], str]
) -> tuple[str, str, str] | None:
    """``(commit, normalized, original)`` of the PR title this decision restates, if any."""
    sha = _sha(_first_commit(d))
    if sha is None:
        return None
    on_commit = _titles_on(pr_titles, sha)
    commit = sha[:_MIN_SHA_PREFIX]
    if len(on_commit) == 1:
        return (commit, *next(iter(on_commit.items())))
    own = normalize(d.get("title", "")).split()
    scored = sorted(((jaccard(own, norm.split()), norm) for norm in on_commit), reverse=True)
    if not scored or scored[0][0] < _MIN_TITLE_OVERLAP:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    best = scored[0][1]
    return commit, best, on_commit[best]


def _add(
    index: dict[str, dict[str, str]],
    value: object,
    title: str,
    normalize: Callable[[str], str],
) -> None:
    norm = normalize(title)
    sha = _sha(value)
    if sha is not None and norm:
        index.setdefault(sha, {}).setdefault(norm, title)


def _sha(value: object) -> str | None:
    if isinstance(value, str) and len(value) >= _MIN_SHA_PREFIX:
        return value.lower()
    return None


def _titles_on(index: dict[str, dict[str, str]], sha: str) -> dict[str, str]:
    """Distinct titles (normalized -> original) on every sha sharing a prefix with *sha*."""
    found: dict[str, str] = {}
    for key, titles in index.items():
        if key.startswith(sha) or sha.startswith(key):
            for norm, title in titles.items():
                found.setdefault(norm, title)
    return found


def _record_commit(rec: DecisionRecord) -> object:
    try:
        commits = json.loads(rec.evidence_commits_json or "[]")
    except ValueError:
        return None
    return commits[0] if isinstance(commits, list) and commits else None


def merge_folded_files(rec: DecisionRecord, members: list[dict]) -> None:
    """Add the files of folded archaeology members to a PR record's scope."""
    extra = [f for d in members if d.get(FOLDED_INTO_PR) for f in d.get("affected_files") or []]
    if not extra or rec.source != "pr":
        return
    try:
        files = json.loads(rec.affected_files_json or "[]")
    except ValueError:
        return
    merged = list(dict.fromkeys([*files, *extra]))
    if merged != files:
        rec.affected_files_json = json.dumps(merged)


#: A prefix with fewer content words ("we now use it") opens too many
#: unrelated sentences.
_MIN_PREFIX_CONTENT_WORDS = 3

#: A span cut mid-sentence starts on the connective that joined it.
_LEADING_CONNECTIVES = frozenset({"also", "and", "but", "so", "then", "thus"})

#: Measured on 462 mined records of repowise-dev/repowise (2026-11-02): pairs
#: at or above this share one sentence reworded; below it, down to 0.6, sit a
#: multi-claim span and one of the claims it bundles, which must stay apart.
_MIN_WORD_OVERLAP = 0.75

#: A set this small says too little for an overlap ratio to mean anything.
_MIN_CONTENT_WORDS = 3

_WORD = re.compile(r"[a-z0-9_]+")

_NEGATIONS = frozenset({"never", "no", "not", "without"})

#: Stems whose ``n't`` does not leave the verb: "won't", "shan't", "can't".
_IRREGULAR_NOT = {"wo": "will", "sha": "shall", "ca": "can"}

#: "can't", "won't", "isn't" (straight or curly apostrophe) and "cannot".
_CONTRACTED_NOT = re.compile(r"\b([a-z]+?)n['\u2019]t\b|\b(can)not\b")


def _expand_not(text: str) -> str:
    """*text* with every ``n't`` contraction and "cannot" spelled as "<verb> not"."""

    def spell(m: re.Match[str]) -> str:
        stem = m.group(1) or m.group(2)
        return f"{_IRREGULAR_NOT.get(stem, stem)} not"

    return _CONTRACTED_NOT.sub(spell, text)


#: Negation, contrast and quantity words are deliberately absent: "use A, not B"
#: and "use B, not A", or "one worker" and "two workers", must not reduce to one
#: word set.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "did",
        "does",
        "done",
        "for",
        "from",
        "had",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "now",
        "of",
        "on",
        "or",
        "our",
        "per",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "they",
        "this",
        "to",
        "use",
        "used",
        "uses",
        "via",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "would",
        "also",
    ]
)


def commit_keys(commits: Iterable[str] | None) -> frozenset[str]:
    """The comparable form of a record's evidence commits."""
    return frozenset(
        c.strip().lower()[:_MIN_SHA_PREFIX]
        for c in commits or ()
        if isinstance(c, str) and len(c.strip()) >= _MIN_SHA_PREFIX
    )


def _words(text: str) -> list[str]:
    words = _WORD.findall(_expand_not((text or "").lower()))
    while words and words[0] in _LEADING_CONNECTIVES:
        words = words[1:]
    return words


def _content(words: list[str]) -> frozenset[str]:
    return frozenset(w for w in words if len(w) >= 2 and w not in _STOPWORDS)


def same_decision_text(a: str, b: str) -> bool:
    """Whether *a* and *b* state one decision in two spans or two wordings."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    ca, cb = _content(wa), _content(wb)
    if long_[: len(short)] == short and len(_content(short)) >= _MIN_PREFIX_CONTENT_WORDS:
        return True
    if min(len(ca), len(cb)) < _MIN_CONTENT_WORDS:
        return False
    # A word set has no order, so a negation is the one difference it must
    # not average away: "the queue is used" against "the queue is not used".
    if ca & _NEGATIONS != cb & _NEGATIONS:
        return False
    return jaccard(ca, cb) >= _MIN_WORD_OVERLAP


#: Measured on the same-lane, same-commit pairs of a store of ~1.5k records
#: (2026-11-28): at or above this, 69 of 69 title pairs named one decision;
#: from 0.5 down sit two choices from one PR ("Exclude co-change edges from
#: regeneration walks" against "... from decision centrality").
_MIN_TITLE_OVERLAP_SAME_COMMIT = 0.6

#: Number words a title can differ by alone: "one worker" and "two workers"
#: share every other word and are still two rulings.
_QUANTITIES = frozenset(
    {"zero", "one", "two", "three", "four", "five", "single", "both", "all", "every", "only"}
)


#: Verbs that reverse a ruling while leaving the rest of the title alone:
#: "Enable auth for health endpoints" and "Disable auth for health endpoints".
#: A fixed list, so an opposite missing from it is not caught; extend it here.
_OPPOSITES = (
    ("add", "remove"),
    ("allow", "block"),
    ("allow", "deny"),
    ("enable", "disable"),
    ("include", "exclude"),
    ("keep", "drop"),
    ("keep", "remove"),
    ("show", "hide"),
    ("start", "stop"),
)


def _quantities(words: list[str]) -> frozenset[str]:
    """Number words and digits in *words*, read before ``_content`` drops a one-digit number."""
    return frozenset(w for w in words if w in _QUANTITIES or w.isdigit())


def _opposed(a: frozenset[str], b: frozenset[str]) -> bool:
    """Whether one title holds a word the other holds the opposite of, and not both."""
    return any((x in a - b and y in b - a) or (y in a - b and x in b - a) for x, y in _OPPOSITES)


def same_decision_title(a: str, b: str) -> bool:
    """Whether titles *a* and *b*, mined from one commit, name the same choice."""
    wa, wb = _words(a), _words(b)
    ca, cb = _content(wa), _content(wb)
    if min(len(ca), len(cb)) < _MIN_CONTENT_WORDS:
        return False
    if ca & _NEGATIONS != cb & _NEGATIONS or _quantities(wa) != _quantities(wb):
        return False
    if _opposed(ca, cb):
        return False
    return jaccard(ca, cb) >= _MIN_TITLE_OVERLAP_SAME_COMMIT


class RewordingIndex:
    """Kept records by lane and evidence commit, matched against a reworded arrival.

    Within one lane only: a PR body against a PR body, a commit against a
    commit. Across lanes the pairing is :func:`fold_archaeology_into_pr`'s.
    Holds only records that may absorb another. The caller adds them in the
    order it prefers them as keepers, and a match returns the earliest added,
    so the record a person accepted, or the oldest reading, stays the record.
    """

    def __init__(self) -> None:
        self._by_commit: dict[str, list[tuple[int, str, str, str]]] = {}
        self._added = 0

    def add(
        self,
        record_id: str,
        source: str,
        commits: Iterable[str] | None,
        text: str,
        title: str = "",
    ) -> None:
        if not text:
            return
        entry = (self._added, record_id, text, title)
        for key in commit_keys(commits):
            self._by_commit.setdefault(f"{source}:{key}", []).append(entry)
        self._added += 1

    def match(
        self, source: str, commits: Iterable[str] | None, text: str, title: str = ""
    ) -> str | None:
        """The id of a kept record this one rewords, or ``None``.

        A text match wins over a title match: the text is the stronger
        evidence, so an older record whose title merely sounds alike must not
        take an arrival from the record that holds its sentence.
        """
        if not text:
            return None
        kept = [
            entry
            for key in commit_keys(commits)
            for entry in self._by_commit.get(f"{source}:{key}", ())
        ]
        by_text = [(o, rid) for o, rid, kept_text, _ in kept if same_decision_text(text, kept_text)]
        hits = by_text or [
            (o, rid) for o, rid, _, kept_title in kept if same_decision_title(title, kept_title)
        ]
        return min(hits)[1] if hits else None
