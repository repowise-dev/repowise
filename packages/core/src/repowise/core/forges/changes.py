"""Change-request numbers (PRs, MRs) in commit messages, read across forges.

Two questions, kept apart:

- ``change_number``: the one change a commit *is*, from its merge or squash
  message. A wrong number is worse than none, so a mention never counts: a
  bare ``#12`` names an issue as often as a PR, and a link names another change.
- ``change_refs``: every change a message mentions, for matching a revert to
  what it undid.

Both read every forge's conventions with the repo's own forge first, since
history can predate a move between forges. A form that only means a change on
one forge (GitLab's ``!12``) counts only when the repo is on that forge.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache

from .base import ForgeKind
from .registry import all_forges, get_forge

#: Lowercase markers that make a commit body read like a PR or MR description
#: worth mining for decisions, rather than an incidental multi-line message.
CHANGE_BODY_MARKERS: tuple[str, ...] = (
    "## why",
    "## motivation",
    "## what",
    "## changes",
    "## context",
    "## summary",
    "closes #",
    "fixes #",
    "resolves #",
    "before:",
    "after:",
)


#: The literal text a pattern opens with (``^`` aside), escapes still in.
_LEAD_RE = re.compile(r"\^?((?:[^\\.^$*+?{}\[\]|()]|\\[^\w])*)")


def lead_of(p: re.Pattern[str]) -> str:
    """The text *p* cannot match without, from its literal opening.

    Raises for a pattern with none (or a case-blind one): every merge form
    must have a lead, so a message is rejected by string tests alone.
    """
    m = _LEAD_RE.match(p.pattern)
    raw = m.group(1) if m else ""
    # A quantifier right after the lead makes its last character optional.
    if raw and p.pattern[m.end() : m.end() + 1] in ("*", "?", "{"):
        raw = raw[:-2] if raw.endswith("\\", 0, len(raw) - 1) else raw[:-1]
    lead = re.sub(r"\\(.)", r"\1", raw)
    if not lead or p.flags & re.IGNORECASE or p.groups != 1:
        raise ValueError(f"a merge form needs a literal lead and one group: {p.pattern!r}")
    return lead


@dataclass(frozen=True, slots=True)
class _Forms:
    """One side's merge forms, each behind a string test it cannot match without.

    Most messages fail every form, and one ``str.startswith`` or ``in`` rejects
    them several times faster than a regex call. Forms anchored to the start
    are tried first, then the rest, each group in the order given.
    """

    starts: tuple[str, ...]
    anchored: tuple[tuple[str, re.Pattern[str]], ...]
    floating: tuple[tuple[str, re.Pattern[str]], ...]
    #: Take a floating form's last match: a body's own trailer closes it, and
    #: one quoted above it (a description, a squashed log) names another change.
    last: bool = False

    @classmethod
    def of(cls, patterns: Iterable[re.Pattern[str]], *, last: bool = False) -> _Forms:
        pairs = [(lead_of(p), p) for p in dict.fromkeys(patterns)]
        anchored = tuple(x for x in pairs if x[1].pattern.startswith("^"))
        floating = tuple(x for x in pairs if not x[1].pattern.startswith("^"))
        return cls(tuple(dict.fromkeys(lead for lead, _ in anchored)), anchored, floating, last)

    def first(self, text: str) -> int | None:
        if text.startswith(self.starts):
            for lead, p in self.anchored:
                if text.startswith(lead) and (m := p.match(text)):
                    return int(m.group(1))
        for lead, p in self.floating:
            if lead not in text:
                continue
            m = None
            if self.last:
                for m in p.finditer(text):  # noqa: B007 - the last one is wanted
                    pass
            else:
                m = p.search(text)
            if m:
                return int(m.group(1))
        return None


# Forges register on package import, before any message is read, so the
# forms per forge are fixed once built.
@cache
def _merge_forms(forge: ForgeKind | str) -> tuple[_Forms, _Forms, bool]:
    """``(subject forms, body forms, body first)`` for a repo on *forge*.

    Keyed by the caller's value as given: a ``ForgeKind`` and its string are
    one key, so the hot path skips converting it per commit.
    """
    first = get_forge(forge)
    order = [first, *(f for f in all_forges() if f is not first)]
    subject = [*first.native_merge_subject_res, *(p for f in order for p in f.merge_subject_res)]
    body = _Forms.of((p for f in order for p in f.merge_body_res), last=True)
    return _Forms.of(subject), body, first.merge_body_first


def change_number(
    subject: str, body: str = "", forge: ForgeKind | str = ForgeKind.GENERIC
) -> int | None:
    """The PR or MR this commit merged, or ``None`` when its message does not say."""
    subject_forms, body_forms, body_first = _merge_forms(forge)
    if body_first and body and (found := body_forms.first(body)) is not None:
        return found
    found = subject_forms.first(subject) if subject else None
    if found is None and body and not body_first:
        found = body_forms.first(body)
    return found


def change_refs(
    subject: str, body: str = "", forge: ForgeKind | str = ForgeKind.GENERIC
) -> list[int]:
    """Every PR or MR the message mentions: the repo's forge's first, then in order."""
    kind = ForgeKind(forge)
    first = get_forge(kind)
    found: dict[int, None] = {}
    for f in (first, *(f for f in all_forges() if f.kind is not kind)):
        for n in f.parse_change_refs(subject, body, native=f is first):
            found.setdefault(n, None)
    return list(found)


__all__ = ["CHANGE_BODY_MARKERS", "change_number", "change_refs"]
