"""Whether the rule a decision states still holds in the code its scope names.

Churn (:mod:`.staleness`) says a record's files moved since it was made; it
cannot say whether what moved was the rule. This reads the rule's own anchors
instead: the identifiers and literals its text names, and, for a record whose
evidence is a comment in the code, that comment. Deterministic, git and the
parser only, no model call.

Three verdicts:

* ``holds``: no change since the anchor touched the code the rule points at
  (``spans_unchanged``), or the comment it was mined from is still there and
  nothing it names went away (``quote_present``).
* ``drifted``: something the rule rests on is gone: the comment it was read
  from (``quote_gone``), a name it cites (``symbol_gone:<name>``), or a literal
  it states, removed by a change to the code it points at (``literal_gone:<text>``).
* ``unknown``: the rule names nothing checkable, or its code was touched and
  nothing provably went away. The caller keeps the churn verdict for it.

A quote from a pull request, a commit message or a session is prose about the
code, never text in it, so only evidence written in a code file is looked for.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import structlog

from repowise.core.analysis.changed_lines import DIFF_ARGS, FileDiff, parse_unified_diff
from repowise.core.analysis.decisions.lifecycle import (
    RULE_DRIFTED as DRIFTED,
)
from repowise.core.analysis.decisions.lifecycle import (
    RULE_HOLDS as HOLDS,
)
from repowise.core.analysis.decisions.lifecycle import (
    RULE_UNKNOWN as UNKNOWN,
)
from repowise.core.analysis.decisions.provenance import normalize_text
from repowise.core.analysis.git_cat_file import cat_file_batch
from repowise.core.analysis.git_cli import run_git
from repowise.core.symbol_spans import symbol_at

logger = structlog.get_logger(__name__)

__all__ = ["CODE_QUOTE_SOURCES", "RuleCheck", "RuleClaim", "check_rules", "commit_times"]

#: Evidence sources whose quote is text in a code file, so it can be looked for
#: there. Everything else (pr, commit, session, agent, cli, adr) is prose.
CODE_QUOTE_SOURCES = frozenset({"code_comment", "comment", "inline_marker"})

#: A function longer than this is a module in all but name: a rule comment
#: inside it governs the statements near it, not every line of the body.
_MAX_GOVERNED_SPAN = 120
#: Lines after a comment block in which the symbol it documents may start.
_DOC_GAP = 3
#: A quote shorter than this matches by accident.
_MIN_QUOTE = 12
#: What a stored quote cut at its length cap can end in.
_CUT_MARKS = chr(0x2026) + chr(0xFFFD) + ". "  # an ellipsis, or half a character
#: An edited comment is the same comment when it still opens or closes with
#: this many of the quote's characters, within this multiple of its length,
#: and keeps this share of its words.
_EDIT_ANCHOR = 40
_EDIT_REACH = 1.5
_EDIT_KEPT = 1.0
_TOKEN_RE = re.compile(r"\w+")
#: Paths per git command line: Windows caps one at 32k characters.
_ARGV_CHUNK = 200


@dataclass(frozen=True, slots=True)
class RuleClaim:
    """What one record says and where, as the check reads it."""

    #: The record's own statements: title and decision.
    texts: tuple[str, ...]
    #: ``(file, verbatim quote)`` for evidence written in a code file.
    quotes: tuple[tuple[str, str], ...]
    #: Files the rule can live in: the record's scope, test and docs files left out.
    scope: tuple[str, ...]
    #: When the record was last known to hold: birth, landing or latest grant.
    anchor: datetime


@dataclass(frozen=True, slots=True)
class RuleCheck:
    verdict: str
    basis: str
    #: The HEAD commit the verdict was read at.
    commit: str = ""


# ---------------------------------------------------------------------------
# What a rule names
# ---------------------------------------------------------------------------

_BACKTICK_RE = re.compile(r"`{1,2}([^`\n]+?)`{1,2}")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_CAMEL_RE = re.compile(r"[a-z0-9][A-Z]|[A-Z]{2}[a-z]")
_NUMBER_RE = re.compile(r"(?<![\w.])\d[\d_]*(?:\.\d+)?(?![\w.]|\.\d)")
_QUOTED_RE = re.compile(r"\"([^\"\n]{2,60})\"|'([^'\n]{2,60})'")
_IDENT_FULL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def _code_shaped(token: str) -> bool:
    """Whether a bare word reads as code: snake, camel or dotted, never an English word."""
    if len(token) < 4 or token.endswith("."):
        return False
    if "." in token:
        return all(token.split(".")) and not token.replace(".", "").isdigit()
    return "_" in token.strip("_") or bool(_CAMEL_RE.search(token))


def cited_identifiers(texts: Iterable[str]) -> set[str]:
    """Identifiers a rule names: backticked names plus snake, camel and dotted words."""
    out: set[str] = set()
    for text in texts:
        for span in _BACKTICK_RE.findall(text):
            span = span.strip().rstrip("()")
            if _IDENT_FULL.fullmatch(span) and len(span) >= 3 and "/" not in span:
                out.add(span)
        for word in _WORD_RE.findall(_BACKTICK_RE.sub(" ", text)):
            if _code_shaped(word):
                out.add(word)
    return out


def cited_literals(texts: Iterable[str]) -> set[str]:
    """Literals a rule states: numbers of two digits or more, quoted strings, code spans."""
    out: set[str] = set()
    for text in texts:
        for span in _BACKTICK_RE.findall(text):
            span = span.strip()
            if not _IDENT_FULL.fullmatch(span) and "/" not in span and len(span) >= 3:
                out.add(span)
        bare = _BACKTICK_RE.sub(" ", text)
        out.update(n for n in _NUMBER_RE.findall(bare) if len(n.replace("_", "")) >= 2)
        for double, single in _QUOTED_RE.findall(bare):
            value = double or single
            if " " not in value.strip():
                out.add(value)
    return out


def _word_re(token: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w]){re.escape(token)}(?![\w])")


# ---------------------------------------------------------------------------
# Finding a quote in a file
# ---------------------------------------------------------------------------

_COMMENT_LEAD = re.compile(r"^(?:/\*\*?|\*/|\*|///?|#+|--|;+|\"\"\"|'''|<!--|-->)\s?")
_COMMENT_TAIL = re.compile(r"\s*(?:\*/|\"\"\"|'''|-->)$")


def _comment_body(line: str) -> str:
    """*line* less one leading comment marker: ``* // x`` keeps the ``//`` it quotes."""
    text = _COMMENT_LEAD.sub("", line.strip(), count=1).strip()
    return _COMMENT_TAIL.sub("", text)


@dataclass
class _Text:
    """One file's text, with a comment-marker-free normalised stream for quote lookup."""

    raw: str
    _stream: str = ""
    _offsets: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        # A blank comment line between paragraphs adds nothing, so the stream
        # stays single-spaced like the normalised quote it is searched for.
        parts: list[str] = []
        pos = 0
        for line in self.raw.splitlines():
            body = normalize_text(_comment_body(line))
            self._offsets.append(pos)
            if body:
                parts.append(body)
                pos += len(body) + 1
        self._stream = " ".join(parts)

    def locate(self, quote: str, *, edited: bool = False) -> tuple[int, int] | None:
        """The 1-indexed inclusive line range holding *quote*, or None.

        With *edited*, a comment that still opens or closes with the quote's
        words and keeps nearly all of them counts as the same comment: a clause
        added to it, or a line rewrapped, leaves the rule it states in place.
        """
        # A stored quote cut at its length cap ends in an ellipsis.
        q = normalize_text(_comment_body(quote.rstrip(_CUT_MARKS)))
        if len(q) < _MIN_QUOTE:
            return None
        at, size = self._stream.find(q), len(q)
        if at < 0 and edited:
            at, size = self._edited(q)
        if at < 0:
            return None
        start = bisect.bisect_right(self._offsets, at)
        end = bisect.bisect_right(self._offsets, at + size - 1)
        return start, end

    def _edited(self, q: str) -> tuple[int, int]:
        """Where an edited copy of *q* sits, as ``(offset, length)``; ``(-1, 0)`` if none."""
        wanted = set(_TOKEN_RE.findall(q))
        reach = int(len(q) * _EDIT_REACH)
        candidates = []
        if (head := self._stream.find(q[:_EDIT_ANCHOR])) >= 0:
            candidates.append(head)
        if (tail := self._stream.find(q[-_EDIT_ANCHOR:])) >= 0:
            candidates.append(max(0, tail + _EDIT_ANCHOR - reach))
        for start in candidates:
            window = self._stream[start : start + reach]
            if len(wanted & set(_TOKEN_RE.findall(window))) >= _EDIT_KEPT * len(wanted):
                return start, len(window)
        return -1, 0


# ---------------------------------------------------------------------------
# Git reads
# ---------------------------------------------------------------------------


def _first_parent_line(repo: str, head: str) -> list[tuple[int, str]]:
    """``(commit time, sha)`` on *head*'s first-parent line, oldest first."""
    shown = run_git(["log", "--first-parent", "--format=%ct %H", head], repo)
    if shown.returncode != 0:
        return []
    rows = [line.split() for line in shown.stdout.splitlines() if line.strip()]
    return sorted((int(ct), sha) for ct, sha in rows)


def _base_commit(line: list[tuple[int, str]], when: datetime) -> str | None:
    """The newest first-parent commit at or before *when*, or None if *when* predates them."""
    at = bisect.bisect_right(line, (int(when.timestamp()), "~"))  # after any hex sha
    return line[at - 1][1] if at else None


def _diff(repo: str, base: str, head: str, paths: list[str]) -> dict[str, FileDiff] | None:
    out: dict[str, FileDiff] = {}
    for at in range(0, len(paths), _ARGV_CHUNK):
        chunk = paths[at : at + _ARGV_CHUNK]
        shown = run_git(
            ["diff", "--unified=0", "--no-renames", *DIFF_ARGS, base, head, "--", *chunk], repo
        )
        if shown.returncode != 0:
            return None
        out.update(parse_unified_diff(shown.stdout))
    return out


def commit_times(repo_path: Path | str, shas: Iterable[str]) -> dict[str, datetime]:
    """Commit time of each of *shas* git holds; the rest are absent."""
    shas = sorted(set(shas))
    if not shas:
        return {}
    shown = run_git(["log", "--no-walk=unsorted", "--format=%H %ct", *shas], str(repo_path))
    if shown.returncode != 0:
        return {}
    rows = (line.split() for line in shown.stdout.splitlines() if line.strip())
    times = {sha: datetime.fromtimestamp(int(ct), UTC) for sha, ct in rows}
    # Stored shas are full, but answer for whatever spelling was asked.
    return {asked: times[full] for asked in shas for full in times if full.startswith(asked)}


def _blobs(repo: str, rev: str, paths: Iterable[str]) -> dict[str, str]:
    specs = sorted(set(paths))
    read = cat_file_batch(repo, [f"{rev}:{p}" for p in specs]) or []
    return {
        spec.split(":", 1)[1]: data.decode("utf-8", "replace")
        for spec, data in read
        if data is not None
    }


def _present_anywhere(repo: str, head: str, names: Iterable[str]) -> set[str]:
    """Which of *names* occur as whole words anywhere in the tree at *head*."""
    names = sorted(set(names))
    found: set[str] = set()
    for at in range(0, len(names), _ARGV_CHUNK):
        args = ["grep", "-h", "-o", "-w", "-F", "-I"]
        for name in names[at : at + _ARGV_CHUNK]:
            args += ["-e", name]
        found.update(line.strip() for line in run_git([*args, head, "--"], repo).stdout.splitlines())
    return found & set(names)


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


@dataclass
class _Reader:
    """Git reads shared by every claim in one run."""

    repo: str
    head: str
    _head_text: dict[str, _Text | None] = field(default_factory=dict)
    _base_text: dict[tuple[str, str], _Text | None] = field(default_factory=dict)
    _spans: dict[str, list] = field(default_factory=dict)
    _parser: object = None

    def load_head(self, paths: Iterable[str]) -> None:
        wanted = [p for p in set(paths) if p not in self._head_text]
        got = _blobs(self.repo, self.head, wanted)
        for p in wanted:
            self._head_text[p] = _Text(got[p]) if p in got else None

    def load_base(self, base: str, paths: Iterable[str]) -> None:
        wanted = [p for p in set(paths) if (base, p) not in self._base_text]
        got = _blobs(self.repo, base, wanted)
        for p in wanted:
            self._base_text[(base, p)] = _Text(got[p]) if p in got else None

    def head_text(self, path: str) -> _Text | None:
        return self._head_text.get(path)

    def base_text(self, base: str, path: str, changed: bool) -> _Text | None:
        return self._base_text.get((base, path)) if changed else self._head_text.get(path)

    def symbols(self, path: str) -> list:
        """Symbols in *path* at head, from the repository's own parser; [] when it cannot parse."""
        if path not in self._spans:
            text = self._head_text.get(path)
            spans: list = []
            if text is not None:
                if self._parser is None:
                    from repowise.core.ingestion.change_detector import ChangeDetector

                    self._parser = ChangeDetector(Path(self.repo))
                try:
                    parsed = self._parser.parse_bytes(text.raw.encode("utf-8"), path)
                    spans = [s for s in (parsed.symbols if parsed else []) if s.start_line > 0]
                except Exception as exc:  # a file the parser rejects has no spans
                    logger.debug("rule_check.parse_failed", path=path, error=str(exc))
            self._spans[path] = spans
        return self._spans[path]


def _quote_spans(
    reader: _Reader, path: str, quotes: list[tuple[int, int]]
) -> tuple[list[tuple[int, int]], bool]:
    """Each quote's lines plus the code it governs, and whether any quote governs code.

    A comment inside a function governs that function, or the lines after it
    when the function is too long to read as one rule; a comment just above a
    symbol documents it. A comment at the top of a module sits on no code, so
    its presence says nothing about what the code does.
    """
    symbols = reader.symbols(path)
    spans: list[tuple[int, int]] = []
    governs = False
    for start, end in quotes:
        spans.append((start, end))
        enclosing = symbol_at(symbols, start)
        documented = [s for s in symbols if end < s.start_line <= end + _DOC_GAP]
        if documented:
            first = min(documented, key=lambda s: s.start_line)
            spans.append((first.start_line, min(first.end_line, first.start_line + _MAX_GOVERNED_SPAN)))
        elif enclosing is not None and enclosing.end_line - enclosing.start_line <= _MAX_GOVERNED_SPAN:
            spans.append((enclosing.start_line, enclosing.end_line))
        elif enclosing is not None:
            spans.append((start, end + _DOC_GAP))
        else:
            continue
        governs = True
    return spans, governs


def _occurrences(text: _Text, names: Iterable[str]) -> list[tuple[int, int]]:
    patterns = [_word_re(n) for n in names]
    return [
        (i, i)
        for i, line in enumerate(text.raw.splitlines(), 1)
        if any(p.search(line) for p in patterns)
    ]


def _hunks(diff: FileDiff) -> Iterable[tuple[int, int, list[str]]]:
    """``(lo, hi, changed lines)`` per hunk, head side; a deletion spans the lines either side of it."""
    removed, added = iter(diff.removed), iter(diff.added)
    for _old_start, old_count, new_start, new_count in diff.hunks:
        lines = [next(removed, "") for _ in range(old_count)]
        lines += [next(added, "") for _ in range(new_count)]
        yield new_start, new_start + max(new_count, 2) - 1, lines


def _overlaps(lo: int, hi: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(start <= hi and lo <= end for start, end in spans)


def _names_any(lines: list[str], tokens: Iterable[str]) -> bool:
    text = "\n".join(lines)
    return any(_word_re(t).search(text) for t in tokens)


def _check_one(
    reader: _Reader, claim: RuleClaim, base: str, diffs: Mapping[str, FileDiff]
) -> tuple[RuleCheck | None, set[str]]:
    """The verdict, or None with the names whose absence decides it, read from git."""
    files = list(dict.fromkeys([*claim.scope, *(f for f, _ in claim.quotes)]))
    names = cited_identifiers([*claim.texts, *(q for _, q in claim.quotes)])
    literals = cited_literals(claim.texts)

    def at_base(path: str) -> _Text | None:
        return reader.base_text(base, path, path in diffs)

    quote_spans: dict[str, list[tuple[int, int]]] = {}
    for path, quote in claim.quotes:
        before = at_base(path)
        if before is None or before.locate(quote) is None:
            continue  # never in the code as written, so nothing to look for now
        after = reader.head_text(path)
        if after is None:
            continue  # the file itself moved; the HEAD artifact check owns that
        found = after.locate(quote, edited=True)
        if found is None:
            return RuleCheck(DRIFTED, "quote_gone"), set()
        quote_spans.setdefault(path, []).append(found)

    anchored = {
        n for n in names if any((t := at_base(p)) is not None and _word_re(n).search(t.raw) for p in files)
    }
    absent = {
        n
        for n in anchored
        if not any((t := reader.head_text(p)) is not None and _word_re(n).search(t.raw) for p in files)
    }
    if absent:
        return None, absent
    if not anchored and not quote_spans:
        return RuleCheck(UNKNOWN, "no_anchor"), set()

    # The rule is touched where a change lands on a line naming what it cites,
    # or on the code a quoted comment governs. A name defined in the scope is
    # read inside its own definition, not at its callers, and only on lines that
    # name what the rule cites: a parameter added to a function the rule names
    # leaves the rule as it was.
    home: dict[str, set[str]] = {}
    for path in files:
        text = reader.head_text(path)
        for name in anchored:
            leaf = name.rsplit(".", 1)[-1]
            if (
                text is not None
                and _word_re(name).search(text.raw)
                and any(s.name in (name, leaf) for s in reader.symbols(path))
            ):
                home.setdefault(name, set()).add(path)
    literals = {
        lit for lit in literals if any((t := at_base(p)) is not None and _word_re(lit).search(t.raw) for p in files)
    }
    used = anchored - home.keys()
    governed = {path: _quote_spans(reader, path, found) for path, found in quote_spans.items()}
    on_code = any(governs for _spans, governs in governed.values())
    touched: list[FileDiff] = []
    for path in files:
        diff = diffs.get(path)
        text = reader.head_text(path)
        if diff is None or text is None:
            continue
        own = {n for n, paths in home.items() if path in paths}
        leaves = {n.rsplit(".", 1)[-1] for n in own} | own
        bodies = [(s.start_line, s.end_line) for s in reader.symbols(path) if s.name in leaves]
        commented = governed.get(path, ([], False))[0]
        uses = _occurrences(text, used)
        for lo, hi, lines in _hunks(diff):
            if (
                _overlaps(lo, hi, commented)
                or _overlaps(lo, hi, uses)
                or _names_any(lines, used | literals)
                or (_overlaps(lo, hi, bodies) and _names_any(lines, own))
            ):
                touched.append(diff)
                break
    if not touched:
        return RuleCheck(HOLDS, "spans_unchanged"), set()
    for literal in sorted(literals):
        pattern = _word_re(literal)
        for diff in touched:
            after = reader.head_text(diff.path)
            if any(pattern.search(line) for line in diff.removed) and not (
                after is not None and pattern.search(after.raw)
            ):
                return RuleCheck(DRIFTED, f"literal_gone:{literal}"), set()
    if on_code:
        return RuleCheck(HOLDS, "quote_present"), set()
    return RuleCheck(UNKNOWN, "spans_touched"), set()


def check_rules(
    repo_path: Path | str, claims: Mapping[str, RuleClaim], *, head: str = "HEAD"
) -> dict[str, RuleCheck]:
    """A verdict per claim id. Ids absent from the result could not be read from git.

    One first-parent log, then per distinct anchor commit one ``git diff`` and
    one blob batch over the union of its claims' files, so the cost follows the
    number of distinct anchors, not the number of records.
    """
    repo = str(repo_path)
    line = _first_parent_line(repo, head)
    if not line or not claims:
        return {}
    head_sha = line[-1][1]
    reader = _Reader(repo, head_sha)

    by_base: dict[str, list[str]] = {}
    for cid, claim in claims.items():
        base = _base_commit(line, claim.anchor)
        if base is not None:
            by_base.setdefault(base, []).append(cid)

    reader.load_head(p for c in claims.values() for p in (*c.scope, *(f for f, _ in c.quotes)))
    out: dict[str, RuleCheck] = {}
    pending: dict[str, set[str]] = {}
    for base, ids in by_base.items():
        paths = sorted({p for cid in ids for p in claims[cid].scope} | {f for cid in ids for f, _ in claims[cid].quotes})
        if base == head_sha:
            out.update((cid, RuleCheck(HOLDS, "spans_unchanged")) for cid in ids)
            continue
        diffs = _diff(repo, base, head_sha, paths)
        if diffs is None:
            continue
        reader.load_base(base, diffs)
        for cid in ids:
            verdict, absent = _check_one(reader, claims[cid], base, diffs)
            if verdict is not None:
                out[cid] = verdict
            else:
                pending[cid] = absent

    # A cited name missing from the record's files may have moved, not gone:
    # one grep over the whole tree settles every such record at once.
    present = _present_anywhere(repo, head_sha, {n for names in pending.values() for n in names})
    for cid, absent in pending.items():
        gone = sorted(absent - present)
        out[cid] = (
            RuleCheck(DRIFTED, f"symbol_gone:{gone[0]}") if gone else RuleCheck(UNKNOWN, "moved")
        )
    return {cid: replace(found, commit=head_sha) for cid, found in out.items()}

