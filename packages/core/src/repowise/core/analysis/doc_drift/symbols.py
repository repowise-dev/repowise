"""Symbol references: a backticked identifier that named code when it was written.

Shape is not evidence. Backticks mean "literal", so a token the index lacks is
usually a word, a key or an example. A candidate becomes a finding only on
proof from git:

1. The index knows no symbol by that name, and no tracked file other than a
   document mentions it as a word. A mention may be a definition the index
   skipped, so a mentioned name is not treated as a reference at all.
2. ``git blame`` names the commit that last wrote the document line.
3. At that commit a non-test source file, parsed with the repository's own
   parser, defines a symbol with exactly that name. Otherwise the token was
   never a symbol reference and is dropped, not reported.

The suggestion follows the defining file to HEAD and keeps the one same-kind
symbol added since whose name is close to the old one.

Process count does not grow with the number of names: one working-tree grep,
one blame per document (line ranges chunked), one grep over every blame commit
and one ``cat-file --batch`` for every file read. Every limit is a count, so a
run is deterministic; work past a limit is uncheckable as ``over-budget``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from repowise.core.ingestion.change_detector import ChangeDetector
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.models import ParsedFile
from repowise.core.test_paths import is_test_related_path

from .constants import MARKDOWN_SUFFIXES
from .extractor import symbol_name
from .models import DocReference, DriftVerdict
from .resolver import Resolution
from .suggest import NO_SUGGESTION, RenameLookup, Suggestion, git_renames, git_run

_MAX_NAMES = 2000
_MAX_DOCUMENTS = 300
_MAX_COMMITS = 200
#: A name more source files than this mention at its commit is too common to confirm.
_MAX_FILES_PER_NAME = 25
_MAX_PARSES = 400
_MAX_RENAME_PATHS = 50
_LINES_PER_BLAME = 400
_GIT_TIMEOUT_SECONDS = 60
#: A safety net only; the count limits above decide every normal run.
_SAFETY_SECONDS = 300.0
_RENAME_RATIO = 0.8

_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SOURCE_EXTS = REGISTRY.extensions_for(REGISTRY.code_languages())

_OVER_BUDGET = "over-budget"


@dataclass(frozen=True)
class SymbolMiss:
    """What a ``MISSING`` symbol reference carries beyond its resolution."""

    evidence: str
    """Where and at which commit the name was defined."""
    defined_in: tuple[str, ...]
    """The defining files at that commit, and where they live now."""
    suggestion: Suggestion = NO_SUGGESTION


#: Per reference: ``None`` when it is not a symbol reference, else the verdict
#: and, for ``MISSING``, what it carries.
_Outcome = tuple[Resolution, SymbolMiss | None] | None
_Definition = tuple[str, ParsedFile]


@dataclass(frozen=True)
class SymbolRecheck:
    """Which symbol references an incremental update re-resolves.

    A reference's verdict can only change when its document changes, or when
    its name enters or leaves a non-document file. Every other reference's
    stored finding is carried forward by the write.
    """

    documents: frozenset[str]
    names: frozenset[str] | None
    """Identifiers the update added to or removed from a non-document file;
    ``None`` when the diff could not be read, which rechecks every document."""

    def whole(self, doc: str) -> bool:
        """Whether every reference in *doc* is re-resolved."""
        return self.names is None or doc in self.documents

    def wants(self, doc: str, name: str) -> bool:
        return self.whole(doc) or name in (self.names or ())


@dataclass(frozen=True)
class SymbolOptions:
    """What the ``symbol`` kind needs from the caller running the pass."""

    names: Collection[str]
    """Every symbol name the index holds."""
    recheck: SymbolRecheck | None = None
    """On an incremental update, which references to re-resolve; ``None``
    re-resolves every one."""


def symbol_recheck(
    root: Path, base_ref: str | None, changed_paths: Iterable[str]
) -> SymbolRecheck:
    """The :class:`SymbolRecheck` for an update from *base_ref* to the working tree."""
    documents = frozenset(p for p in changed_paths if _is_document(p))
    if not base_ref:
        return SymbolRecheck(documents, None)
    ran = git_run(
        root, "diff", "-U0", "--no-color", "--no-ext-diff", "-M", base_ref, "--",
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    if ran is None or ran[0] != 0:
        return SymbolRecheck(documents, None)
    diff = ran[1].decode("utf-8", errors="replace")
    return SymbolRecheck(documents, frozenset(_names_entering_or_leaving(diff)))


def _names_entering_or_leaving(diff: str) -> set[str]:
    """Identifiers a ``-U0`` diff adds to or removes from some non-document file.

    Per file, a name on both removed and added lines is still in that file, so
    only the names on one side can have entered or left it.
    """
    names: set[str] = set()
    for block in diff.split("\ndiff --git "):
        header, _, body = block.partition("\n")
        if _is_document(header.rsplit(" b/", 1)[-1]):
            continue
        sides: dict[str, set[str]] = {"+": set(), "-": set()}
        for line in body.splitlines():
            if line[:1] in sides and not line.startswith(("+++", "---")):
                sides[line[0]].update(_WORD_RE.findall(line[1:]))
        names |= sides["+"] ^ sides["-"]
    return names


def graph_symbol_names(graph: Any) -> frozenset[str]:
    """Every symbol name an ingestion graph holds, as the index's ``wiki_symbols`` does."""
    return frozenset(
        data["name"]
        for _, data in graph.nodes(data=True)
        if data.get("node_type") == "symbol"
        and data.get("name")
        and data["name"] != "__module__"
    )


def rewrite_symbol(raw: str, name: str, new: str) -> str:
    """*raw* with its final identifier *name* replaced by *new*, qualifier and ``()`` kept."""
    tail = "()" if raw.endswith("()") else ""
    head = raw[: len(raw) - len(tail) - len(name)]
    return f"{head}{new}{tail}"


def _is_document(path: str) -> bool:
    dot = path.rfind(".")
    return dot > path.rfind("/") and path[dot:].lower() in MARKDOWN_SUFFIXES


def _is_source(path: str) -> bool:
    dot = path.rfind(".")
    return dot > path.rfind("/") + 1 and path[dot:].lower() in _SOURCE_EXTS


class _Budget:
    def __init__(self) -> None:
        self.parses = 0
        self.deadline = time.monotonic() + _SAFETY_SECONDS

    def parse(self, n: int) -> bool:
        """Accept *n* more parses when they fit; count them only then."""
        if self.parses + n > _MAX_PARSES:
            return False
        self.parses += n
        return True

    def timed_out(self) -> bool:
        return time.monotonic() > self.deadline


def resolve_symbols(
    refs: Sequence[DocReference],
    *,
    root: Path,
    symbol_names: Collection[str],
    rename_lookup: RenameLookup | None = None,
) -> list[_Outcome]:
    """One outcome per ``SYMBOL`` reference in *refs*, aligned with it."""
    run = _Run(refs, root)
    pending = run.index_filter(symbol_names)
    by_doc = run.unmentioned(pending)
    by_commit = run.blame(by_doc)
    missing = run.definitions(by_commit)
    run.assemble(missing, rename_lookup)
    return run.out


class _Run:
    """One pass over the symbol references, as named steps.

    Each step records the references it settles in :attr:`out` and hands the
    rest on; a step given nothing does nothing.
    """

    def __init__(self, refs: Sequence[DocReference], root: Path) -> None:
        self.refs = refs
        self.root = root
        self.out: list[_Outcome] = [None] * len(refs)
        self.names = {i: symbol_name(ref.raw) for i, ref in enumerate(refs)}
        self.budget = _Budget()
        self.detector = ChangeDetector(root)

    def settle(self, idxs: Iterable[int], verdict: DriftVerdict, detail: str) -> None:
        for i in idxs:
            self.out[i] = (Resolution(self.refs[i], verdict, detail), None)

    def index_filter(self, symbol_names: Collection[str]) -> list[int]:
        """Resolve what the index names; the rest, within the name limit, are pending."""
        pending: list[int] = []
        wanted: set[str] = set()
        for i, name in self.names.items():
            if name in symbol_names:
                self.settle([i], DriftVerdict.RESOLVED, "indexed-symbol")
            elif name in wanted or len(wanted) < _MAX_NAMES:
                wanted.add(name)
                pending.append(i)
            else:
                self.settle([i], DriftVerdict.UNCHECKABLE, _OVER_BUDGET)
        return pending

    def unmentioned(self, pending: Sequence[int]) -> dict[str, list[int]]:
        """Pending references whose name no non-document file mentions, by document."""
        if not pending:
            return {}
        mentioned = _head_mentions(self.root, sorted({self.names[i] for i in pending}))
        if mentioned is None:
            self.settle(pending, DriftVerdict.UNCHECKABLE, "git-failed")
            return {}
        by_doc: dict[str, list[int]] = {}
        for i in pending:
            if self.names[i] not in mentioned:  # a mentioned name is not a reference
                by_doc.setdefault(self.refs[i].doc_path, []).append(i)
        return by_doc

    def blame(self, by_doc: Mapping[str, list[int]]) -> dict[str, list[int]]:
        """References grouped by the commit that last wrote their line."""
        by_commit: dict[str, list[int]] = {}
        for n, (doc, idxs) in enumerate(sorted(by_doc.items())):
            if n >= _MAX_DOCUMENTS or self.budget.timed_out():
                self.settle(idxs, DriftVerdict.UNCHECKABLE, _OVER_BUDGET)
                continue
            shas = _blame(self.root, doc, sorted({self.refs[i].line for i in idxs}))
            if shas is None:
                self.settle(idxs, DriftVerdict.UNCHECKABLE, "git-failed")
                continue
            for i in idxs:
                sha = shas.get(self.refs[i].line, "")
                if sha.strip("0"):
                    by_commit.setdefault(sha, []).append(i)
                else:
                    self.settle([i], DriftVerdict.UNCHECKABLE, "uncommitted")
        for sha in sorted(by_commit)[_MAX_COMMITS:]:
            self.settle(by_commit.pop(sha), DriftVerdict.UNCHECKABLE, _OVER_BUDGET)
        return by_commit

    def definitions(
        self, by_commit: Mapping[str, list[int]]
    ) -> dict[int, tuple[str, list[_Definition]]]:
        """References whose name a source file defined at their commit."""
        if not by_commit:
            return {}
        names = {sha: {self.names[i] for i in idxs} for sha, idxs in by_commit.items()}
        found = _definitions(self.root, self.detector, names, self.budget)
        if found is None:
            all_idxs = [i for idxs in by_commit.values() for i in idxs]
            self.settle(all_idxs, DriftVerdict.UNCHECKABLE, "git-failed")
            return {}
        missing: dict[int, tuple[str, list[_Definition]]] = {}
        for sha, idxs in by_commit.items():
            for i in idxs:
                defs = found.get((sha, self.names[i]))
                if defs is None:
                    self.settle([i], DriftVerdict.UNCHECKABLE, _OVER_BUDGET)
                elif defs:
                    missing[i] = (sha, defs)
                # ``[]``: never a definition, so not a symbol reference.
        return missing

    def assemble(
        self,
        missing: Mapping[int, tuple[str, list[_Definition]]],
        rename_lookup: RenameLookup | None,
    ) -> None:
        """Record each missing reference with its evidence and any rename suggestion."""
        renamed = _renamed_paths(self.root, missing.values(), rename_lookup)
        for i, (sha, defs) in sorted(missing.items()):
            paths = sorted({path for path, _ in defs})
            new = _renamed_symbol(self.detector, self.names[i], defs, renamed, self.budget)
            suggestion: Suggestion = NO_SUGGESTION
            if new:
                suggestion = (rewrite_symbol(self.refs[i].raw, self.names[i], new), "symbol_rename")
            miss = SymbolMiss(
                evidence=f"defined in {', '.join(paths)} at {sha[:12]}",
                defined_in=tuple(sorted({*paths, *(renamed[p] for p in paths if p in renamed)})),
                suggestion=suggestion,
            )
            res = Resolution(
                self.refs[i], DriftVerdict.MISSING, "no-definition", "symbol_no_definition"
            )
            self.out[i] = (res, miss)


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def _grep(root: Path, revs: Sequence[str], names: Sequence[str]) -> list[tuple[str, str]] | None:
    """``(location, line)`` for every line mentioning one of *names* as a word.

    *revs* empty greps the working tree, tracked and untracked; ``None`` when
    the grep failed, so a failure never reads as "absent".
    """
    # One alternation on one line: git tries each ``-f`` line as a separate
    # pattern, so a line per name costs a pass per name.
    alternation = "|".join(re.escape(n) for n in names)
    pattern = f"(^|[^A-Za-z0-9_])({alternation})([^A-Za-z0-9_]|$)\n".encode()
    where = list(revs) or ["--untracked"]
    ran = git_run(
        root, "grep", "-E", "-I", "-z", "-f", "-", *where,
        stdin=pattern, timeout=_GIT_TIMEOUT_SECONDS,
    )
    if ran is None or ran[0] not in (0, 1):
        return None
    lines = ran[1].decode("utf-8", errors="replace").splitlines()
    return [(loc, text) for loc, sep, text in (ln.partition("\0") for ln in lines) if sep]


def _head_mentions(root: Path, names: Sequence[str]) -> set[str] | None:
    """Which of *names* any non-document file in the working tree mentions."""
    found = _grep(root, (), names)
    if found is None:
        return None
    wanted = set(names)
    mentioned: set[str] = set()
    for path, text in found:
        if not _is_document(path):
            mentioned |= wanted.intersection(_WORD_RE.findall(text))
    return mentioned


def _commit_mentions(
    root: Path, shas: Sequence[str], names: Sequence[str]
) -> dict[tuple[str, str], set[str]] | None:
    """``{(commit, name): non-test source paths mentioning it}`` over every commit."""
    found = _grep(root, shas, names)
    if found is None:
        return None
    wanted = set(names)
    out: dict[tuple[str, str], set[str]] = {}
    for location, text in found:
        sha, _, path = location.partition(":")
        if not _is_source(path) or is_test_related_path(path):
            continue
        for name in wanted.intersection(_WORD_RE.findall(text)):
            out.setdefault((sha, name), set()).add(path)
    return out


def _blame(root: Path, doc: str, lines: Sequence[int]) -> dict[int, str] | None:
    """The commit that last wrote each of *lines* of *doc*."""
    shas: dict[int, str] = {}
    for start in range(0, len(lines), _LINES_PER_BLAME):
        chunk = lines[start : start + _LINES_PER_BLAME]
        ranges = [arg for n in chunk for arg in ("-L", f"{n},{n}")]
        ran = git_run(
            root, "blame", "--porcelain", "-w", *ranges, "--", doc, timeout=_GIT_TIMEOUT_SECONDS
        )
        if ran is None or ran[0] != 0:
            return None
        for line in ran[1].decode("utf-8", errors="replace").splitlines():
            parts = line.split(" ")
            if len(parts) in (3, 4) and _SHA_RE.fullmatch(parts[0]) and parts[2].isdigit():
                shas[int(parts[2])] = parts[0]
    return shas


def _definitions(
    root: Path,
    detector: ChangeDetector,
    names: Mapping[str, set[str]],
    budget: _Budget,
) -> dict[tuple[str, str], list[_Definition]] | None:
    """``{(commit, name): definitions}`` confirmed by parsing; ``None`` when git failed.

    *names* maps each commit to the names wanted there. ``[]`` means no non-test
    source file defined the name at that commit; a key left out could not be
    confirmed within the limits.
    """
    mentions = _commit_mentions(root, sorted(names), sorted(set().union(*names.values())))
    if mentions is None:
        return None
    accepted = _accept(names, mentions, budget)
    files = sorted({(sha, p) for (sha, _), paths in accepted.items() for p in paths})
    parsed = _parse_at(root, detector, files)
    if parsed is None:
        return None
    defined = {key: {s.name for s in pf.symbols} for key, pf in parsed.items()}
    return {
        (sha, name): [(p, parsed[(sha, p)]) for p in paths if name in defined.get((sha, p), ())]
        for (sha, name), paths in accepted.items()
    }


def _accept(
    names: Mapping[str, set[str]],
    mentions: Mapping[tuple[str, str], set[str]],
    budget: _Budget,
) -> dict[tuple[str, str], list[str]]:
    """The files to parse per ``(commit, name)``, within the per-name and parse limits."""
    accepted: dict[tuple[str, str], list[str]] = {}
    files: set[tuple[str, str]] = set()
    for sha in sorted(names):
        for name in sorted(names[sha]):
            paths = sorted(mentions.get((sha, name), ()))
            fresh = [p for p in paths if (sha, p) not in files]
            if len(paths) > _MAX_FILES_PER_NAME or not budget.parse(len(fresh)):
                continue
            files.update((sha, p) for p in fresh)
            accepted[(sha, name)] = paths
    return accepted


def _parse_at(
    root: Path, detector: ChangeDetector, files: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], ParsedFile] | None:
    """Each ``(commit, path)`` parsed, from one ``cat-file --batch``."""
    if not files:
        return {}
    request = "".join(f"{sha}:./{path}\n" for sha, path in files).encode("utf-8")
    ran = git_run(root, "cat-file", "--batch", stdin=request, timeout=_GIT_TIMEOUT_SECONDS)
    if ran is None or ran[0] != 0:
        return None
    data, pos, out = ran[1], 0, {}
    # Each object is ``<oid> blob <size>\n<bytes>\n``, or one ``missing`` line.
    for key in files:
        end = data.find(b"\n", pos)
        header = data[pos:end].split(b" ")
        pos = end + 1
        if len(header) != 3 or header[1] != b"blob":
            continue
        size = int(header[2])
        parsed = detector.parse_bytes(data[pos : pos + size], key[1])
        pos += size + 1
        if parsed is not None:
            out[key] = parsed
    return out


# ---------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------


def _renamed_paths(
    root: Path,
    missing: Iterable[tuple[str, list[_Definition]]],
    rename_lookup: RenameLookup | None,
) -> dict[str, str]:
    """Where each defining file lives now: itself when still on disk, else its git rename."""
    paths = sorted({path for _, defs in missing for path, _ in defs})[:_MAX_RENAME_PATHS]
    where = {p: p for p in paths if (root / p).is_file()}
    gone = [p for p in paths if p not in where]
    if gone:
        lookup = rename_lookup or (lambda wanted: git_renames(root, wanted))
        try:
            renames = lookup(gone)
        except Exception:  # a failed lookup costs a suggestion, never the pass
            renames = {}
        where.update({p: new for p, new in renames.items() if (root / new).is_file()})
    return where


def _renamed_symbol(
    detector: ChangeDetector,
    name: str,
    defs: Sequence[_Definition],
    renamed: Mapping[str, str],
    budget: _Budget,
) -> str:
    """The one same-kind symbol added since, closest to *name*, else ``""``.

    Scored on the name alone, so where a symbol sits in the file cannot tip it,
    and a tie between two names is no suggestion.
    """
    found: set[str] = set()
    for path, old in defs:
        new = _parse_now(detector, renamed.get(path), budget)
        if new is None:
            continue
        best = _closest_added(name, old, new)
        if len(best) > 1:
            return ""
        found.update(best)
    return found.pop() if len(found) == 1 else ""


def _parse_now(detector: ChangeDetector, path: str | None, budget: _Budget) -> ParsedFile | None:
    """*path* as it is on disk now, parsed within the parse limit."""
    if not path or not budget.parse(1):
        return None
    try:
        return detector.parse_bytes((detector.repo_path / path).read_bytes(), path)
    except OSError:
        return None


def _closest_added(name: str, old: ParsedFile, new: ParsedFile) -> list[str]:
    """Same-kind names *new* added over *old* that tie for closest to *name* at the ratio."""
    kinds = {s.kind for s in old.symbols if s.name == name}
    before = {s.name for s in old.symbols}
    scores = {
        s.name: SequenceMatcher(None, name.lower(), s.name.lower()).ratio()
        for s in new.symbols
        if s.kind in kinds and s.name not in before
    }
    top = max(scores.values(), default=0.0)
    return [n for n, ratio in scores.items() if ratio == top and ratio >= _RENAME_RATIO]
