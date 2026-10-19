"""The files a synthesized answer rests on, read from the answer itself.

Synthesis sees more than the retrieval hits: the structured prelude and the
caller graph name files no hit carries. Citing only the hits the answer
mentions would cite a hit the answer rules out ("cascade.py does not compute
it") and miss the file it names as the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# A file name with an extension, optionally under directories.
_PATH_RE = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]*\w\.[A-Za-z0-9]{1,6})(?![\w/])")

# A dismissal right before or after a path: "not in X", "rather than X",
# "X does not compute it", "X is not where ...". A negated description ("X has
# no backoff") is how an answer explains a cause, so it rules nothing out
# (ceiling: "X does not handle it" still reads as a dismissal).
_NEG_BEFORE_RE = re.compile(
    r"(?:\bnot\s+(?:in|from|by|inside)|\brather\s+than|\binstead\s+of|\boutside(?:\s+of)?|"
    r"\bother\s+than)\s+(?:the\s+)?(?:file\s+)?[`'\"(]*$",
    re.IGNORECASE,
)
_NEG_AFTER_RE = re.compile(
    r"^[`'\")]*\s*(?:\([^)]{0,40}\)\s*)?(?:itself\s+|also\s+|only\s+)?"
    r"(?:does\s+not|doesn't|is\s+not|isn't|never)\s+(?:\w+\s+){0,3}?"
    r"(?:it|this|that|them|where|responsible|involved)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AnswerFiles:
    """``citations``: the files the answer rests on, in the order it names them.
    ``ruled_out``: files the answer says are not the answer. ``named``: whether
    the answer named any file, rather than the citations being the fallback."""

    citations: list[str]
    ruled_out: frozenset[str]
    named: bool

    def drops_top_hit(self, hits: list[dict], answer_text: str) -> str | None:
        """The file the answer names first when it rules out or never names the top hit.

        The top hit is the file retrieval dominance was measured on, so a grade
        drawn from it does not vouch for an answer resting elsewhere. Any
        mention of the top hit's file name counts as naming it, even one that
        resolves to no single file.
        """
        top = hits[0].get("target_path") if hits else None
        if not (self.named and top) or top in self.citations:
            return None
        if top not in self.ruled_out and re.search(
            rf"(?<![\w.-]){re.escape(top.rsplit('/', 1)[-1])}(?!\w)", answer_text
        ):
            return None
        return self.citations[0]


def _is_ruled_out(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 40) : start]
    after = text[end : end + 60]
    return bool(_NEG_BEFORE_RE.search(before) or _NEG_AFTER_RE.search(after))


def _resolve(named: str, hit_paths: list[str], known: list[str], repo_root: Path | None) -> str | None:
    """``named`` as an indexed path: exact, a unique suffix of one, or a live file.

    A bare file name resolves only to the one hit that carries it: against the
    whole repository it is too loose to name a file.
    """
    named = named.removeprefix("./")
    if ".." in named.split("/"):
        return None
    if "/" not in named:
        same = [p for p in hit_paths if p.rsplit("/", 1)[-1] == named]
        return same[0] if len(same) == 1 else None
    if named in known:
        return named
    suffixed = [k for k in known if k.endswith("/" + named)]
    if len(suffixed) == 1:
        return suffixed[0]
    if repo_root is not None:
        try:
            if (repo_root / named).is_file():
                return named
        except OSError:
            return None
    return None


def answer_files(
    answer_text: str,
    hits: list[dict],
    pool: list[dict],
    fallback_targets: list[str],
    repo_root: Path | None,
) -> AnswerFiles:
    """The files ``answer_text`` names, minus the ones it rules out.

    A named path resolves against the hits, then the wider ranked pool, then
    the live checkout, so an invented path is never cited. A hit whose path
    appears verbatim stays cited, as before. With nothing named, the top two
    fallback targets the answer does not rule out are cited instead.
    """
    hit_paths = [h["target_path"] for h in hits if h.get("target_path")]
    known = list(dict.fromkeys(hit_paths + [h["target_path"] for h in pool if h.get("target_path")]))
    mentions = [
        (path, _is_ruled_out(answer_text, m.start(1), m.end(1)))
        for m in _PATH_RE.finditer(answer_text or "")
        if (path := _resolve(m.group(1), hit_paths, known, repo_root))
    ]
    named = [p for p, _ in mentions] + [
        h["target_path"] for h in hits if h.get("target_path") and h["target_path"] in answer_text
    ]
    # A file the answer relies on in one sentence and dismisses in another
    # stays cited: only a file it never names positively is ruled out.
    ruled = {p for p, neg in mentions if neg} - {p for p, neg in mentions if not neg}
    citations = [p for p in dict.fromkeys(named) if p not in ruled]
    if citations:
        return AnswerFiles(citations, frozenset(ruled), named=True)
    fallback = [p for p in fallback_targets if p not in ruled][:2] or fallback_targets[:2]
    return AnswerFiles(fallback, frozenset(ruled), named=False)
