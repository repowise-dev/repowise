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
import os
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


#: Paths in a ``git diff`` pathspec are file names, never globs or magic.
_LITERAL_PATHS = {**os.environ, "GIT_LITERAL_PATHSPECS": "1"}


def _rev_parse(repo: str, rev: str) -> str | None:
    shown = run_git(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], repo)
    return (shown.stdout.strip() or None) if shown.returncode == 0 else None


def _first_parent_line(repo: str, head: str) -> list[tuple[int, str]]:
    """``(commit time, sha)`` on *head*'s first-parent line, sorted for bisecting by time."""
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
            ["diff", "--unified=0", "--no-renames", *DIFF_ARGS, base, head, "--", *chunk],
            repo,
            env=_LITERAL_PATHS,
        )
        if shown.returncode != 0:
            return None
        out.update(parse_unified_diff(shown.stdout))
    return out


def commit_times(repo_path: Path | str, shas: Iterable[str]) -> dict[str, datetime]:
    """Commit time of each of *shas* git holds; the rest are absent."""
    shas = sorted(set(shas))
    times: dict[str, datetime] = {}
    for at in range(0, len(shas), _ARGV_CHUNK):
        chunk = shas[at : at + _ARGV_CHUNK]
        shown = run_git(
            ["log", "--no-walk=unsorted", "--ignore-missing", "--format=%H %ct", *chunk],
            str(repo_path),
        )
        if shown.returncode == 0:
            rows = (line.split() for line in shown.stdout.splitlines() if line.strip())
            times.update((sha, datetime.fromtimestamp(int(ct), UTC)) for sha, ct in rows)
    # Stored shas are full, but answer for whatever spelling was asked.
    return {asked: times[full] for asked in shas for full in times if full.startswith(asked)}


def _blobs(repo: str, rev: str, paths: Iterable[str]) -> dict[str, str] | None:
    """Each path's text at *rev*; a path *rev* lacks is absent. None when git failed."""
    specs = sorted(set(paths))
    read = cat_file_batch(repo, [f"{rev}:{p}" for p in specs])
    if read is None or len(read) != len(specs):
        return None
    return {
        spec.split(":", 1)[1]: data.decode("utf-8", "replace")
        for spec, data in read
        if data is not None
    }


def _present_anywhere(repo: str, head: str, names: Iterable[str]) -> set[str] | None:
    """Which of *names* occur as whole words anywhere in the tree at *head*; None if git failed."""
    names = sorted(set(names))
    found: set[str] = set()
    for at in range(0, len(names), _ARGV_CHUNK):
        args = ["grep", "-h", "-o", "-w", "-F", "-I"]
        for name in names[at : at + _ARGV_CHUNK]:
            args += ["-e", name]
        shown = run_git([*args, head, "--"], repo)
        if shown.returncode > 1:  # 1 is "no match"
            return None
        found.update(line.strip() for line in shown.stdout.splitlines())
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

    # A failed read reports False and records nothing: an unread file must
    # never pass for one that is absent, which would read the rule as untouched.
    def load_head(self, paths: Iterable[str]) -> bool:
        wanted = [p for p in set(paths) if p not in self._head_text]
        got = _blobs(self.repo, self.head, wanted)
        if got is None:
            return False
        for p in wanted:
            self._head_text[p] = _Text(got[p]) if p in got else None
        return True

    def load_base(self, base: str, paths: Iterable[str]) -> bool:
        wanted = [p for p in set(paths) if (base, p) not in self._base_text]
        got = _blobs(self.repo, base, wanted)
        if got is None:
            return False
        for p in wanted:
            self._base_text[(base, p)] = _Text(got[p]) if p in got else None
        return True

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


@dataclass
class _Claim:
    """One claim read against one anchor commit."""

    reader: _Reader
    claim: RuleClaim
    base: str
    diffs: Mapping[str, FileDiff]

    @property
    def files(self) -> list[str]:
        return list(dict.fromkeys([*self.claim.scope, *(f for f, _ in self.claim.quotes)]))

    def at_base(self, path: str) -> _Text | None:
        return self.reader.base_text(self.base, path, path in self.diffs)

    def in_base(self, token: str) -> bool:
        pattern = _word_re(token)
        return any((t := self.at_base(p)) is not None and pattern.search(t.raw) for p in self.files)

    def in_head(self, token: str) -> bool:
        pattern = _word_re(token)
        return any(
            (t := self.reader.head_text(p)) is not None and pattern.search(t.raw)
            for p in self.files
        )

    def quotes_at_head(self) -> dict[str, list[tuple[int, int]]] | None:
        """Head lines of each quote that was in the code at the anchor; None if one is gone."""
        found: dict[str, list[tuple[int, int]]] = {}
        for path, quote in self.claim.quotes:
            before, after = self.at_base(path), self.reader.head_text(path)
            # Never in the code as written, or the file itself moved (the HEAD
            # artifact check owns that): nothing to look for.
            if before is None or after is None or before.locate(quote) is None:
                continue
            where = after.locate(quote, edited=True)
            if where is None:
                return None
            found.setdefault(path, []).append(where)
        return found

    def homes(self, names: set[str]) -> dict[str, set[str]]:
        """The scope files that define each of *names*."""
        out: dict[str, set[str]] = {}
        for path in self.files:
            text = self.reader.head_text(path)
            if text is None:
                continue
            defined = {s.name for s in self.reader.symbols(path)}
            for name in names:
                if {name, name.rsplit(".", 1)[-1]} & defined and _word_re(name).search(text.raw):
                    out.setdefault(name, set()).add(path)
        return out


def _touching_hunks(
    read: _Claim,
    path: str,
    home: dict[str, set[str]],
    tokens: tuple[set[str], set[str]],
    commented: list[tuple[int, int]],
) -> list[list[str]]:
    """The changed lines of each hunk in *path* that lands on what the rule points at.

    *tokens* is the names the scope only uses and the literals the rule states.
    """
    diff, text = read.diffs.get(path), read.reader.head_text(path)
    if diff is None or text is None:
        return []
    used, literals = tokens
    own = {n for n, paths in home.items() if path in paths}
    leaves = {n.rsplit(".", 1)[-1] for n in own} | own
    bodies = [(s.start_line, s.end_line) for s in read.reader.symbols(path) if s.name in leaves]
    uses = _occurrences(text, used)
    return [
        lines
        for lo, hi, lines in _hunks(diff)
        if _overlaps(lo, hi, commented)
        or _overlaps(lo, hi, uses)
        or _names_any(lines, used | literals)
        or (_overlaps(lo, hi, bodies) and _names_any(lines, own))
    ]


def _literal_gone(read: _Claim, literals: set[str], touched: dict[str, list[list[str]]]) -> str | None:
    """A stated literal a hunk on the rule removed and the file no longer has anywhere."""
    for literal in sorted(literals):
        pattern = _word_re(literal)
        for path, hunks in touched.items():
            after = read.reader.head_text(path)
            removed = any(pattern.search(line) for lines in hunks for line in lines)
            if removed and not (after is not None and pattern.search(after.raw)):
                return literal
    return None


def _check_one(read: _Claim) -> tuple[RuleCheck | None, set[str]]:
    """The verdict, or None with the names whose absence decides it, read from git."""
    claim = read.claim
    quotes = read.quotes_at_head()
    if quotes is None:
        return RuleCheck(DRIFTED, "quote_gone"), set()
    cited = cited_identifiers([*claim.texts, *(q for _, q in claim.quotes)])
    anchored = {n for n in cited if read.in_base(n)}
    absent = {n for n in anchored if not read.in_head(n)}
    if absent:
        return None, absent
    if not anchored and not quotes:
        return RuleCheck(UNKNOWN, "no_anchor"), set()

    # The rule is touched where a change lands on a line naming what it cites,
    # or on the code a quoted comment governs. A name defined in the scope is
    # read inside its own definition, not at its callers, and only on lines that
    # name what the rule cites: a parameter added to a function the rule names
    # leaves the rule as it was.
    home = read.homes(anchored)
    tokens = (anchored - home.keys(), {lit for lit in cited_literals(claim.texts) if read.in_base(lit)})
    governed = {path: _quote_spans(read.reader, path, found) for path, found in quotes.items()}
    touched = {}
    for path in read.files:
        commented = governed.get(path, ([], False))[0]
        if hunks := _touching_hunks(read, path, home, tokens, commented):
            touched[path] = hunks
    if not touched:
        return RuleCheck(HOLDS, "spans_unchanged"), set()
    if (literal := _literal_gone(read, tokens[1], touched)) is not None:
        return RuleCheck(DRIFTED, f"literal_gone:{literal}"), set()
    if any(governs for _spans, governs in governed.values()):
        return RuleCheck(HOLDS, "quote_present"), set()
    return RuleCheck(UNKNOWN, "spans_touched"), set()


def _check_base(
    reader: _Reader, claims: Mapping[str, RuleClaim], base: str, ids: list[str]
) -> tuple[dict[str, RuleCheck], dict[str, set[str]]]:
    """Verdicts for the claims anchored at *base*, and the names each still has to find."""
    if base == reader.head:
        return {cid: RuleCheck(HOLDS, "spans_unchanged") for cid in ids}, {}
    paths = sorted({p for cid in ids for p in claims[cid].scope} | {f for cid in ids for f, _ in claims[cid].quotes})
    diffs = _diff(reader.repo, base, reader.head, paths)
    if diffs is None or not reader.load_base(base, diffs):
        return {}, {}
    out: dict[str, RuleCheck] = {}
    pending: dict[str, set[str]] = {}
    for cid in ids:
        verdict, absent = _check_one(_Claim(reader, claims[cid], base, diffs))
        if verdict is not None:
            out[cid] = verdict
        else:
            pending[cid] = absent
    return out, pending


def _read_verdicts(repo: str, head_sha: str, claims: Mapping[str, RuleClaim]) -> dict[str, RuleCheck]:
    """The verdicts git could read; a claim it could not read is absent."""
    line = _first_parent_line(repo, head_sha)
    reader = _Reader(repo, head_sha)
    by_base: dict[str, list[str]] = {}
    for cid, claim in claims.items():
        if (base := _base_commit(line, claim.anchor)) is not None:
            by_base.setdefault(base, []).append(cid)
    if not by_base or not reader.load_head(
        p for c in claims.values() for p in (*c.scope, *(f for f, _ in c.quotes))
    ):
        return {}
    out: dict[str, RuleCheck] = {}
    pending: dict[str, set[str]] = {}
    for base, ids in by_base.items():
        decided, left = _check_base(reader, claims, base, ids)
        out.update(decided)
        pending.update(left)

    # A cited name missing from the record's files may have moved, not gone:
    # one grep over the whole tree settles every such record at once. When git
    # cannot answer, nothing is called gone.
    present = _present_anywhere(repo, head_sha, {n for names in pending.values() for n in names})
    for cid, absent in pending.items():
        gone = sorted(absent - present) if present is not None else []
        out[cid] = (
            RuleCheck(DRIFTED, f"symbol_gone:{gone[0]}") if gone else RuleCheck(UNKNOWN, "moved")
        )
    return out


def check_rules(
    repo_path: Path | str, claims: Mapping[str, RuleClaim], *, head: str = "HEAD"
) -> dict[str, RuleCheck]:
    """A verdict per claim id, read at *head*; empty only when *head* cannot be read.

    One first-parent log, then per distinct anchor commit one ``git diff`` and
    one blob batch over the union of its claims' files, so the cost follows the
    number of distinct anchors, not the number of records. A claim git could
    not read (anchored before the history this checkout holds, or a diff or
    blob read that failed) is ``unknown`` with basis ``unread``: it keeps the
    churn reading, never reads as holding, and carries the commit so the caller
    retries it only once its files move again.
    """
    repo = str(repo_path)
    head_sha = _rev_parse(repo, head) if claims else None
    if head_sha is None:
        return {}
    out = _read_verdicts(repo, head_sha, claims)
    unread = RuleCheck(UNKNOWN, "unread")
    return {cid: replace(out.get(cid, unread), commit=head_sha) for cid in claims}
