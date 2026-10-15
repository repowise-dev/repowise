"""The local suspect signals, registered on ``SeamRegistry("suspect-signal")``.

- ``text``: BM25 (SQLite FTS5, in memory) of the symptom text against each
  change's title, body, file paths and touched symbol names.
- ``graph``: fewest import-graph hops from a symptom file to a changed file.
- ``co-change``: the strongest ROSE-style confidence (Zimmermann et al. 2004)
  of a rule from a symptom file to a changed file: of the symptom file's
  commits, the share that also touched it, minimum support 2.
- ``recency``: newest first.
- ``trace``: changed files and symbols that stack-trace frames name.

A plugin adds a signal with ``suspect_signals.register(name, impl)``; every
registered signal runs and fuses with equal weight.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from datetime import UTC

from repowise.core.co_change import MIN_CO_CHANGE_SUPPORT
from repowise.core.fts_query import PREFIX_MIN_CHARS, build_fts5_query, meaningful_terms

from .rank import Candidate, Hit, PreparedSymptom, SuspectContext, suspect_signals

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SPLIT = re.compile(r"[/\\._\-\s]+")
#: A path-like token with an extension; URLs and absolute paths keep their tail.
_PATH_TOKEN = re.compile(r"[\w@~.\-/\\]*[\w\-]\.[A-Za-z][\w]{0,7}\b")
_MAX_EVIDENCE = 5
#: Words that match nearly every change, so they say nothing as evidence.
_EVIDENCE_STOPWORDS = frozenset(
    {
        "after", "again", "already", "always", "any", "because", "before", "between",
        "both", "cannot", "come", "could", "does", "done", "during", "even", "every",
        "first", "from", "get", "gets", "getting", "got", "here", "into", "like", "make",
        "makes", "many", "more", "most", "much", "need", "needs", "new", "next", "now",
        "off", "once", "one", "only", "other", "out", "over", "same", "see", "seems",
        "shows", "show", "since", "some", "still", "such", "sure", "take", "than", "then",
        "there", "these", "thing", "things", "through", "under", "until", "use", "used",
        "uses", "using", "very", "want", "way", "well", "what", "when", "where", "which",
        "while", "will", "with", "within", "without", "work", "works", "would", "yet"
    }
)


def path_mentions(text: str) -> list[str]:
    """Path-like tokens in free text, in order, deduped."""
    return list(dict.fromkeys(m.group(0).lstrip("/\\") for m in _PATH_TOKEN.finditer(text)))


def _words(text: str) -> str:
    """Path and identifier parts as separate words, the originals kept."""
    parts = _SPLIT.split(text)
    return " ".join([text, *parts, *(_CAMEL.sub(" ", p) for p in parts if _CAMEL.search(p))])


def _document(c: Candidate) -> str:
    return "\n".join([c.title, c.body, *(_words(f) for f in c.files), *(_words(s) for s in c.symbols)])


class TextSignal:
    """BM25 over the candidates of one query; FTS5 is in the stdlib's SQLite."""

    def rank(
        self, symptom: PreparedSymptom, candidates: Sequence[Candidate], ctx: SuspectContext
    ) -> Mapping[str, Hit]:
        text = symptom.symptom.text
        terms = meaningful_terms(text)
        if not terms or not candidates:
            return {}
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE VIRTUAL TABLE doc USING fts5(cid UNINDEXED, body)")
            db.executemany(
                "INSERT INTO doc(cid, body) VALUES (?, ?)",
                [(c.change_id, _document(c)) for c in candidates],
            )
            rows = db.execute(
                "SELECT cid, bm25(doc), body FROM doc WHERE doc MATCH ?", (build_fts5_query(text),)
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        finally:
            db.close()
        wanted = list(dict.fromkeys(terms))
        matched = {cid: [t for t in wanted if _fts_matches(t, _fts_words(body))] for cid, _, body in rows}
        # Evidence only (the score is BM25's): the rarest matched terms first,
        # without the repo's own name or filler words that match every row.
        df = Counter(t for ts in matched.values() for t in ts)
        quiet = _EVIDENCE_STOPWORDS | ctx.repo_terms
        out: dict[str, Hit] = {}
        for cid, score, _body in rows:
            ranked = sorted(matched[cid], key=lambda t: (df[t], wanted.index(t)))
            shown = [t for t in ranked if t not in quiet][:_MAX_EVIDENCE] or ranked[:1]
            out[cid] = Hit(-score, tuple(f"term {t}" for t in shown))
        return out


_FTS_TOKEN = re.compile(r"[^\W_]+")


def _fold(text: str) -> str:
    """Lowercase without diacritics, as FTS5's unicode61 tokenizer reads text."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _fts_words(body: str) -> tuple[list[str], set[str]]:
    words = _FTS_TOKEN.findall(_fold(body))
    return words, set(words)


def _fts_matches(term: str, words: tuple[list[str], set[str]]) -> bool:
    """Whether FTS5 matched *term* here: its tokens as a phrase, the last one by
    prefix when the term is long enough (:func:`~repowise.core.fts_query.match_term`)."""
    parts = _FTS_TOKEN.findall(_fold(term))
    seq, vocab = words
    prefix = len(term) >= PREFIX_MIN_CHARS
    if len(parts) == 1:
        return parts[0] in vocab or (prefix and any(w.startswith(parts[0]) for w in vocab))
    n = len(parts)
    for i in range(len(seq) - n + 1):
        if seq[i : i + n - 1] == parts[:-1] and (
            seq[i + n - 1] == parts[-1] or (prefix and seq[i + n - 1].startswith(parts[-1]))
        ):
            return True
    return False


def _hops(start: Sequence[str], imports: Mapping[str, object]) -> dict[str, tuple[int, str]]:
    """Fewest undirected import hops to each reachable file, and from which start."""
    seen: dict[str, tuple[int, str]] = {s: (0, s) for s in start}
    queue = deque(start)
    while queue:
        node = queue.popleft()
        dist, origin = seen[node]
        for nxt in imports.get(node, ()):  # type: ignore[union-attr]
            if nxt not in seen:
                seen[nxt] = (dist + 1, origin)
                queue.append(nxt)
    return seen


class GraphSignal:
    def rank(
        self, symptom: PreparedSymptom, candidates: Sequence[Candidate], ctx: SuspectContext
    ) -> Mapping[str, Hit]:
        if not symptom.files or not ctx.imports:
            return {}
        reach = _hops(symptom.files, ctx.imports)
        out: dict[str, Hit] = {}
        for c in candidates:
            best = min(((reach[f][0], f, reach[f][1]) for f in c.files if f in reach), default=None)
            if best is not None:
                hops, changed, origin = best
                why = (
                    f"changes {changed}, a symptom file"
                    if hops == 0
                    else f"{hops} import hop{'s' if hops != 1 else ''} from {origin} to {changed}"
                )
                out[c.change_id] = Hit(-hops, (why,))
        return out


class CoChangeSignal:
    def rank(
        self, symptom: PreparedSymptom, candidates: Sequence[Candidate], ctx: SuspectContext
    ) -> Mapping[str, Hit]:
        rules: dict[str, tuple[float, str, int, int]] = {}
        for s in symptom.files:
            for p in ctx.co_change.get(s, ()):
                if p.support < MIN_CO_CHANGE_SUPPORT or p.self_commits <= 0:
                    continue
                conf = min(p.support / p.self_commits, 1.0)
                if conf > rules.get(p.file_path, (0.0,))[0]:
                    rules[p.file_path] = (conf, s, p.support, p.self_commits)
        out: dict[str, Hit] = {}
        for c in candidates:
            best = max((rules[f] + (f,) for f in c.files if f in rules), default=None)
            if best is not None:
                conf, s, support, total, f = best
                out[c.change_id] = Hit(
                    conf, (f"{f} changed in {support} of {total} commits to {s}",)
                )
        return out


class RecencySignal:
    def rank(
        self, symptom: PreparedSymptom, candidates: Sequence[Candidate], ctx: SuspectContext
    ) -> Mapping[str, Hit]:
        out: dict[str, Hit] = {}
        for c in candidates:
            at = c.merged_at if c.merged_at.tzinfo else c.merged_at.replace(tzinfo=UTC)
            hours = max((symptom.at - at).total_seconds() / 3600, 0.0)
            out[c.change_id] = Hit(at.timestamp(), (f"merged {hours:.0f} h before the symptom",))
        return out


class TraceSignal:
    def rank(
        self, symptom: PreparedSymptom, candidates: Sequence[Candidate], ctx: SuspectContext
    ) -> Mapping[str, Hit]:
        if not symptom.frames:
            return {}
        trace_files = set(symptom.trace_files)
        functions = {f.function.rsplit(".", 1)[-1] for f in symptom.frames if f.function}
        out: dict[str, Hit] = {}
        for c in candidates:
            files = [f for f in c.files if f in trace_files]
            symbols = sorted(functions.intersection(c.symbols))
            if files or symbols:
                evidence = [f"frame file {f}" for f in files] + [f"frame function {s}" for s in symbols]
                out[c.change_id] = Hit(len(files) + len(symbols), tuple(evidence[:_MAX_EVIDENCE]))
        return out


suspect_signals.register("text", TextSignal())
suspect_signals.register("graph", GraphSignal())
suspect_signals.register("co-change", CoChangeSignal())
suspect_signals.register("recency", RecencySignal())
suspect_signals.register("trace", TraceSignal())


__all__ = [
    "CoChangeSignal",
    "GraphSignal",
    "RecencySignal",
    "TextSignal",
    "TraceSignal",
    "path_mentions",
]
