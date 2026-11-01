"""The source lines a search for one identifier or literal names.

An identifier that resolves to one to three indexed symbols gets their edit
sets (definition, imports, calls, references) from :mod:`_edit_sites`. A
single-token query naming no indexed symbol (a number, a quoted string, an
unindexed name) is scanned for in every file git lists (tracked and
untracked, ignore rules applied), so config and docs the indexer never parsed
are read too. git grep narrows that list to the files holding the token;
without it the scan reads source files first. ``complete`` is true only when
nothing was skipped or capped, so the lines are every match.
"""

from __future__ import annotations

import asyncio
import codecs
import logging
import os
import re
import subprocess
import time
from collections import Counter
from collections.abc import Container
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.fs_walk import PRUNED_DIRS, walk_repo
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode
from repowise.core.repo_config import load_repo_config
from repowise.core.support_paths import file_population, is_doc_or_config_path
from repowise.core.test_paths import is_test_related_path
from repowise.server.mcp_server._budget import OmissionCollector, register_post_enforce
from repowise.server.mcp_server._edit_sites import (
    _MAX_FILE_BYTES,
    MAX_REFERENCES_PER_FILE,
    MAX_TEXT_CHARS,
    _bare_name,
    _cap_references_per_file,
    _read_lines,
    reference_edit_set,
)
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    is_excluded,
    read_repo_file_text,
)
from repowise.server.mcp_server._meta import served_root
from repowise.server.mcp_server._query_shape import (
    _NL_QUESTION_TOKENS,
    _canonical_symbol_query,
    _identifier_candidates,
    _qual_norm,
)
from repowise.server.mcp_server._symbol_lookup import symbol_id_variants
from repowise.server.mcp_server._verify import name_at_line

_log = logging.getLogger("repowise.mcp.search")

MAX_LINES = 50
# A multi-word query's page rows already rank files, so its lines are a short
# lead-in (production first) and lines_omitted_by_file names the top files only.
MAX_LINES_MIXED = 15
MAX_OMITTED_FILES = 5
OVER_LINES_MIXED = f"over {MAX_LINES_MIXED} lines for a multi-word query; search one name for more"
MAX_LINES_PER_FILE = 5
# A literal set this small comes back whole: the per-file cut only keeps one
# busy file from crowding out the rest of a large set.
SMALL_SET_LINES = 20
MAX_SYMBOLS = 3
MAX_SCAN_FILES = 20_000
MAX_SCAN_BYTES = 32_000_000
# Wall-clock ceiling on the literal scan; past it the lines are marked incomplete.
MAX_SCAN_SECONDS = 0.5
# Past this git grep is abandoned for the Python scan.
_GIT_GREP_SECONDS = 5.0
_SNIFF_BYTES = 8192
# A binary past this is not streamed for the token; it is reported as skipped.
_MAX_STREAM_BYTES = 64_000_000
OVER_LINES = f"over {MAX_LINES} lines"
_KIND_ORDER = {"definition": 0, "import": 1, "call": 2, "reference": 3, "match": 4}
# Under the short mixed-query cap, call sites say more than imports.
_MIXED_KIND_ORDER = {"definition": 0, "call": 1, "import": 2, "reference": 3, "match": 3}
_QUOTED = re.compile(r"""^(["'`])(.+)\1$""")
_TOKEN = re.compile(r"^[A-Za-z0-9_$][\w$.]*$")
# Code punctuation: a leading decorator ``@``, an identifier glued to an opening
# ``(`` or ``{`` (``send(``), or a glued ``=`` (``path="/x"``). Prose such as
# ``Add (optional) support`` has a space before its parenthesis and matches none.
_CODE_PUNCT = re.compile(r"^@\w|[\w)\]][({]|\S=\S")
_DECL_WORDS = r"def|class|function|const|let|var|fn|func|type|interface|struct|enum"


def _literal(query: str) -> tuple[str, bool] | None:
    """``(needle, word_bounded)`` for a single-token or code-shaped query, else ``None``."""
    q = query.strip()
    if m := _QUOTED.match(q):
        return m.group(2), False
    if len(q) >= 2 and _TOKEN.match(q) and not q.endswith("."):
        return q, True
    if len(q) >= 2 and "/" in q and q.strip("./") and not any(char.isspace() for char in q):
        return q, False
    if _is_code_shaped(q):
        return q, False
    return None


def _is_code_shaped(q: str) -> bool:
    """A one-line, non-question query carrying code punctuation, scanned as-is."""
    if len(q) < 3 or "\n" in q or q.endswith("?"):
        return False
    words = q.split()
    if words[0].lower().rstrip(",.;:") in _NL_QUESTION_TOKENS:
        return False
    return _CODE_PUNCT.search(q) is not None


def _definition_re(needle: str, word: bool) -> re.Pattern[str]:
    """A line declaring *needle* as a name, or assigning it as a value."""
    esc = re.escape(needle)
    tail = r"(?![\w$])" if word else ""
    as_name = (
        rf"^\s*(?:[\w$.]+\.)?{esc}\s*=(?![=>])"
        rf"|^\s*[\"']?{esc}[\"']?\s*:"
        rf"|\b(?:{_DECL_WORDS})\s+{esc}{tail}"
    )
    as_value = (
        r"^\s*(?:[A-Za-z_$][\w$]*\s+)*[\"']?[A-Za-z_$][\w$.\-]*[\"']?"
        rf"(?:\s*:\s*[\w.\[\]<>| ]+)?\s*[:=]\s*[\"'`]?{esc}{tail}"
        r"[\"'`]?\s*[,;]?\s*(?:(?://|#).*)?$"
    )
    return re.compile(f"{as_name}|{as_value}" if word else as_value)


def _matching_lines(text: str, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    """``(line number, line)`` for up to one past the small-set size."""
    out: list[tuple[int, str]] = []
    line_no, counted_to, last_start = 1, 0, -1
    for m in pattern.finditer(text):
        start = text.rfind("\n", 0, m.start()) + 1
        if start == last_start:
            continue
        line_no += text.count("\n", counted_to, start)
        counted_to = last_start = start
        end = text.find("\n", start)
        out.append((line_no, text[start : end if end >= 0 else len(text)]))
        if len(out) > max(SMALL_SET_LINES, MAX_LINES_PER_FILE):
            break
    return out


def _inside(rel: str) -> bool:
    """A relative path that cannot step out of the repo root."""
    p = Path(rel)
    return bool(rel) and not (p.is_absolute() or p.drive or p.root or ".." in p.parts)


def _looks_binary(head: bytes) -> bool:
    """A NUL or invalid UTF-8 in the first bytes: a document or archive, not text."""
    if b"\x00" in head:
        return True
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head)
    except UnicodeDecodeError:
        return True
    return False


def _stream_contains(fh: Any, raw: bytes, chunk: int = 1 << 20) -> bool:
    """Whether *fh* holds *raw* anywhere, read from the start in chunks."""
    fh.seek(0)
    tail = b""
    while block := fh.read(chunk):
        if raw in block or raw in tail + block[: len(raw) - 1]:
            return True
        # Keep enough to catch a needle split across two chunks.
        tail = block[-(len(raw) - 1) :] if len(raw) > 1 else b""
    return False


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _git_paths(
    root: Path, args: list[str], timeout: float, ok: tuple[int, ...]
) -> list[str] | None:
    """The NUL-separated paths a git command prints outside pruned dirs, or ``None`` on failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if proc.returncode not in ok:
        return None
    listed = proc.stdout.decode("utf-8", errors="replace").split("\0")
    return [
        p for p in dict.fromkeys(listed) if p and not PRUNED_DIRS.intersection(p.split("/")[:-1])
    ]


def _listed_files(root: Path) -> list[str]:
    """Tracked and untracked files minus ignored ones, or a pruned walk without git."""
    args = ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    listed = _git_paths(root, args, 10, (0,))
    if listed is not None:
        return listed
    return [
        (Path(d) / f).relative_to(root).as_posix() for d, _, names in walk_repo(root) for f in names
    ]


def _files_holding(root: Path, needle: str) -> list[str] | None:
    """The files :func:`_listed_files` would list whose bytes hold *needle*, via git grep.

    ``None`` when git cannot answer in time, for a needle that is not one plain
    argument, or for a non-ASCII one, whose misses in non-UTF-8 files only the
    Python scan can report.
    """
    if not needle.isascii() or "\n" in needle or "\0" in needle or needle.startswith("-"):
        return None
    args = ["-c", "grep.fullName=false", "grep", "-l", "-z", "--untracked", "--no-color", "-F"]
    args += ["-e", needle]
    # Exit 1 is "no file matched".
    return _git_paths(root, args, _GIT_GREP_SECONDS, (0, 1))


def _scan_rank(rel: str) -> int:
    """Production code first, then tests, examples, and docs or config."""
    population = file_population(rel, is_test=is_test_related_path(rel))
    return ("production", "test", "example", "doc").index(population)


def _scan_listed(root: Path, needle: str, word: bool) -> tuple[list[dict[str, Any]], list[str]]:
    held = _files_holding(root, needle)
    files = _listed_files(root) if held is None else held
    # Ordered so a scan that stops on a budget has read source before docs.
    files = sorted(files, key=lambda f: (_scan_rank(f), f))
    patterns = load_repo_config(root).get("exclude_patterns") or []
    reasons: list[str] = []
    if patterns:
        import pathspec

        spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
        kept = [f for f in files if not spec.match_file(f)]
        if len(kept) < len(files):
            # A grep would still read these.
            reasons.append("excluded paths not scanned")
        files = kept
    rows, scan_reasons = scan_literal(root, files, needle, word)
    return rows, reasons + scan_reasons


def word_scan(root: Path, token: str) -> tuple[list[dict[str, Any]], bool]:
    """The word-bounded lines holding *token* in every listed file, from the
    same scan a literal search runs, and whether that scan read them all. For
    get_answer, which reads live lines the wiki never rendered (function
    bodies, string literals). A file cut at the per-file line cap still served
    rows, so it hides no file and leaves the scan complete."""
    rows, reasons = _scan_listed(root, token, True)
    return rows, all(r.startswith(_per_file_cap_reason()) for r in reasons)


def _per_file_cap_reason() -> str:
    """The reason a scan gives for files it cut at the per-file line cap."""
    return f"over {MAX_LINES_PER_FILE} lines in"


def production_only(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*rows* in production files, without tests, examples and docs."""
    return [r for r in rows if _scan_rank(r["path"]) == 0]


def _hit_kinds(
    rel: str, found: list[tuple[int, str]], definition: re.Pattern[str]
) -> list[tuple[int, str, str]]:
    """``(line number, line, kind)`` per hit in *rel*.

    A key in a data file or a doc line is not a definition, whatever its shape.
    """
    if is_doc_or_config_path(rel):
        return [(n, line, "match") for n, line in found]
    return [(n, line, "definition" if definition.search(line) else "match") for n, line in found]


def _line_row(rel: str, n: int, line: str, kind: str) -> dict[str, Any]:
    return {"path": rel, "line": n, "kind": kind, "text": line.strip()[:MAX_TEXT_CHARS]}


def _with_tails(
    rows: list[dict[str, Any]],
    tails: list[tuple[str, list[tuple[int, str]]]],
    definition: re.Pattern[str],
    capped: int,
) -> tuple[list[dict[str, Any]], int]:
    """``(rows, files still cut)``: *rows* with each cut file's remaining lines
    after its own when every cut file served rows and the whole set holds at
    most ``SMALL_SET_LINES`` lines; else *rows* and *capped* unchanged."""
    extra = {rel: _hit_kinds(rel, tail, definition) for rel, tail in tails if tail}
    total = len(rows) + sum(map(len, extra.values()))
    if not extra or len(extra) != capped or total > SMALL_SET_LINES:
        return rows, capped
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        out.append(row)
        last = i + 1 == len(rows) or rows[i + 1]["path"] != row["path"]
        if last and row["path"] in extra:
            out += [_line_row(row["path"], n, line, k) for n, line, k in extra.pop(row["path"])]
    return out, 0


def scan_literal(
    root: Path, files: list[str], needle: str, word: bool
) -> tuple[list[dict[str, Any]], list[str]]:
    """Matching lines in *files*, definitions first, and why they may not be all."""
    pattern = re.compile(rf"(?<![\w$]){re.escape(needle)}(?![\w$])" if word else re.escape(needle))
    definition = _definition_re(needle, word)
    raw = needle.encode("utf-8")
    reasons: list[str] = []
    rows: list[dict[str, Any]] = []
    scanned = 0
    oversized: list[str] = []
    unreadable: list[str] = []
    per_file_capped = overflow = defs = 0
    tails: list[tuple[str, list[tuple[int, str]]]] = []
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    undecodable = binary_hits = 0
    from repowise.core.ingestion.document_extractors import (
        document_extractor_claims,
        is_extracted_document,
    )

    claims = document_extractor_claims()
    for i, rel in enumerate(files):
        if i >= MAX_SCAN_FILES:
            reasons.append(f"over {MAX_SCAN_FILES} files")
            break
        if time.monotonic() > deadline:
            reasons.append(f"time budget: scanned {i} of {len(files)} files")
            break
        if not _inside(rel):
            unreadable.append(rel)
            continue
        try:
            with open(root / rel, "rb") as fh:
                size = os.fstat(fh.fileno()).st_size
                if size > _MAX_FILE_BYTES:
                    if size > _MAX_STREAM_BYTES or not _looks_binary(fh.read(_SNIFF_BYTES)):
                        oversized.append(rel)
                    elif _stream_contains(fh, raw):
                        binary_hits += 1
                    continue
                if scanned + size > MAX_SCAN_BYTES:
                    reasons.append(
                        f"over {MAX_SCAN_BYTES // 1_000_000} MB: scanned {i} of {len(files)}"
                        " files, source first"
                    )
                    break
                scanned += size
                data = fh.read()
        except FileNotFoundError:
            continue  # deleted since indexing: nothing to match
        except OSError:
            # A graph file node can name a package directory; it holds no text.
            if not (root / rel).is_dir():
                unreadable.append(rel)
            continue
        if is_extracted_document(root / rel, data, claims=claims):
            continue
        if b"\x00" in data:
            # Binary: no lines to serve, but one holding the token is a match
            # a grep would report. UTF-16 text is not binary.
            if data.startswith((b"\xff\xfe", b"\xfe\xff")):
                undecodable += 1
            elif raw in data:
                binary_hits += 1
            continue
        if raw not in data:
            # An ASCII needle shows as the same bytes in any ASCII-compatible
            # encoding; a non-ASCII one is missed unless the file is UTF-8.
            if not raw.isascii() and not _is_utf8(data):
                undecodable += 1
            continue
        text = data.decode("utf-8", errors="replace")
        found = _matching_lines(text, pattern)
        tail = found[MAX_LINES_PER_FILE:]
        per_file_capped += bool(tail)
        del found[MAX_LINES_PER_FILE:]
        hits = _hit_kinds(rel, found, definition)
        if len(rows) >= MAX_LINES:
            # Full: only definitions still earn a row, as they sort first.
            kept = [h for h in hits if h[2] == "definition"] if defs < MAX_LINES else []
            overflow += len(hits) - len(kept)
            hits = kept
        if not hits:
            continue
        # Only a file that serves lines pays for the guarded reader: its path
        # resolution is most of a scan's cost when paid for every file.
        if read_repo_file_text(root, rel) is None:
            unreadable.append(rel)
            continue
        defs += sum(h[2] == "definition" for h in hits)
        rows += [_line_row(rel, n, line, k) for n, line, k in hits]
        tails.append((rel, tail))
    rows, per_file_capped = _with_tails(rows, tails, definition, per_file_capped)
    if overflow:
        reasons.append(OVER_LINES)
    if oversized:
        reasons.append(f"skipped as too large: {', '.join(oversized[:3])}")
    if unreadable:
        reasons.append(f"unreadable: {', '.join(unreadable[:3])}")
    if undecodable:
        reasons.append(f"{undecodable} non-UTF-8 text files not scanned")
    if binary_hits:
        reasons.append(f"{binary_hits} binary files contain the token")
    if per_file_capped:
        reasons.append(f"{_per_file_cap_reason()} {per_file_capped} files")
    return rows, reasons


async def _exact_symbols(session, repo_id: str, query: str, candidates: list[str]) -> list[GraphNode]:
    """Graph symbols the query names exactly: by id, by name, or by dotted qualified name."""
    plain = {c for c in candidates if "." not in c}
    dotted = {_qual_norm(c) for c in candidates if "." in c}
    if canonical := _canonical_symbol_query(query):
        clause = GraphNode.node_id.in_(symbol_id_variants(f"{canonical[0]}::{canonical[1]}"))
    else:
        clause = GraphNode.name.in_(plain | {d.rsplit(".", 1)[-1] for d in dotted})
    stmt = select(GraphNode).where(
        GraphNode.repository_id == repo_id, GraphNode.node_type == "symbol", clause
    )
    nodes = (await session.execute(stmt)).scalars().all()
    if canonical:
        return list(nodes)
    return [
        n
        for n in nodes
        if n.name in plain
        or any(
            q == d or q.endswith("." + d)
            for d in dotted
            for q in (_qual_norm(n.qualified_name), _qual_norm(n.node_id))
        )
    ]


def _merge(sets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """One row per line across edit sets, keeping the most specific kind."""
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    reasons: list[str] = []
    for s in sets:
        for site in s["sites"]:
            key = (site["path"], site["line"])
            if key not in seen or _KIND_ORDER[site["kind"]] < _KIND_ORDER[seen[key]["kind"]]:
                seen[key] = site
        reasons += [r for r in s.get("reasons", []) if r not in reasons]
    return list(seen.values()), reasons


async def attach_line_hits(
    response: dict, query: str, mode: str, names: Container[str] | None, repo: str | None
) -> None:
    """Add ``lines``/``complete``/``reasons`` for an identifier or literal query.

    Additive: a failure here leaves the search response as it was.
    """
    try:
        await _attach(response, query, mode, names, repo)
    except Exception:
        _log.warning("search_codebase: line hits failed", exc_info=True)


async def _attach(
    response: dict, query: str, mode: str, names: Container[str] | None, repo: str | None
) -> None:
    if repo == "all":
        return
    literal = _literal(query)
    if mode == "path" and (
        response.get("results") or literal is None or "/" not in literal[0]
    ):
        return
    candidates = _identifier_candidates(query, mode, names)
    if literal and not literal[1] and not _QUOTED.match(query.strip()):
        # Code punctuation names the text itself: an identifier inside it
        # (``copy_with`` in ``copy_with(path="/x")``) is not the question.
        candidates = []
    if not candidates and literal and literal[1]:
        candidates = [literal[0]]
    if not candidates and not literal:
        return
    ctx = await _resolve_repo_context(repo)
    spec = _get_exclude_spec(ctx.path)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        root = Path(served_root(repository, ctx.path))
        found = await _exact_symbols(session, repository.id, query, candidates) if candidates else []
        nodes = [n for n in found if not is_excluded(n.file_path, spec)]
        # Past MAX_SYMBOLS a name is too shared for one edit set per symbol.
        sets = None if len(nodes) > MAX_SYMBOLS else [
            await reference_edit_set(session, repository.id, root, n) for n in nodes
        ]
    served = await _line_rows(root, nodes, sets, literal, response)
    if served is None:
        return
    rows, reasons = served
    mixed = not literal and len(query.split()) > 1
    order = _MIXED_KIND_ORDER if mixed else _KIND_ORDER
    # Production before test within each kind group: every kind for a mixed
    # query, definitions against the rest otherwise.
    rows.sort(
        key=lambda r: (
            order[r["kind"]] if mixed else r["kind"] != "definition",
            is_test_related_path(r["path"]),
            order[r["kind"]],
            r["path"],
            r["line"],
        )
    )
    kept = _cap_references_per_file(rows)
    if len(kept) < len(rows):
        reasons.append(
            f"{len(rows) - len(kept)} references past {MAX_REFERENCES_PER_FILE} per file"
            " not shown: lines_omitted_by_file; the full list is under _meta.omitted"
        )
    cap, over = (MAX_LINES_MIXED, OVER_LINES_MIXED) if mixed else (MAX_LINES, OVER_LINES)
    if len(kept) > cap and over not in reasons:
        reasons.append(over)
    response["lines"] = kept[:cap]
    if len(response["lines"]) < len(rows):
        shown = {id(r) for r in response["lines"]}
        hidden = [r for r in rows if id(r) not in shown]
        collector = OmissionCollector("search_codebase", repo_root=ctx.path)
        collector.add(f"{query} :: lines not shown", hidden)
        collector.attach(response)
        by_file = Counter(r["path"] for r in hidden)
        if mixed and len(by_file) > MAX_OMITTED_FILES:
            response["lines_omitted_by_file"] = dict(by_file.most_common(MAX_OMITTED_FILES))
            response["lines_omitted_by_file_total"] = len(by_file)
        else:
            response["lines_omitted_by_file"] = dict(by_file)
    response["complete"] = not reasons
    if reasons:
        response["reasons"] = reasons
    if response["lines"]:
        _reconcile_symbol_miss(response, literal_scan=not sets)


async def _line_rows(
    root: Path,
    nodes: list[GraphNode],
    sets: list[dict[str, Any]] | None,
    literal: tuple[str, bool] | None,
    response: dict,
) -> tuple[list[dict[str, Any]], list[str]] | None:
    """``(rows, reasons)`` from the edit sets, a shared name's definitions, or a
    literal scan; ``None`` when there is nothing to read."""
    if sets is None:
        return await asyncio.to_thread(_ambiguous_rows, root, nodes, response)
    if sets:
        return _merge(sets)
    if literal:
        return await asyncio.to_thread(_scan_listed, root, *literal)
    return None


def _ambiguous_rows(
    root: Path, nodes: list[GraphNode], response: dict
) -> tuple[list[dict[str, Any]], list[str]]:
    """Definition rows for the *nodes* ``results`` does not show, and the reason saying so.

    Between them ``results`` and these rows name every definition, once.
    """
    shown = {
        (r.get("path") or r.get("file"), r.get("start_line"))
        for r in response.get("results") or ()
        if isinstance(r, dict) and r.get("type") == "symbol"
    }
    rest = [n for n in nodes if (n.file_path, n.start_line) not in shown]
    reason = (
        f"{len(nodes)} symbols share this name: definitions only, {len(nodes) - len(rest)}"
        " in results and the rest here; search a path::Name id for one symbol's sites"
    )
    return _definition_rows(root, rest), [reason]


def _definition_rows(root: Path, nodes: list[GraphNode]) -> list[dict[str, Any]]:
    """A live ``definition`` row per node whose stored line still names it."""
    rows: list[dict[str, Any]] = []
    files: dict[str, list[str] | None] = {}
    for node in nodes[: MAX_LINES + 1]:
        rel, start = node.file_path, node.start_line or 0
        if rel not in files:
            files[rel] = _read_lines(root, rel)
        lines = files[rel]
        if lines and name_at_line(lines, _bare_name(node), start):
            text = lines[start - 1].strip()[:MAX_TEXT_CHARS]
            rows.append({"path": rel, "line": start, "kind": "definition", "text": text})
    return rows


def _reconcile_symbol_miss(response: dict, *, literal_scan: bool) -> None:
    """Keep the symbol stage's miss advice from contradicting the lines found.

    ``grep_hint`` and the no-exact-symbol ``note`` were written before any
    line was read, and tell the reader to recheck spelling or grep. With
    lines in hand both are wrong; ``exact_match: false`` still says no symbol
    has the name, so the note stays only to label fuzzy results.
    """
    response.pop("grep_hint", None)
    if not literal_scan or response.get("exact_match") is not False:
        return
    if response.get("results"):
        response["note"] = (
            "No indexed symbol has this exact name: the results are other "
            "symbols, and `lines` are where the text occurs."
        )
    else:
        response.pop("note", None)


def _lines_after_budget(result: dict[str, Any]) -> None:
    """The final budget guard cut or dropped ``lines``: they are no longer every match.

    Keyed on ``lines`` itself as well as the guard's ``lines_total`` stamp, so
    a drop is caught however the budgeter records it.
    """
    dropped = "lines" not in result and "complete" in result
    if "lines_total" not in result and not dropped:
        return
    result["complete"] = False
    reasons = result.get("reasons") if isinstance(result.get("reasons"), list) else []
    result["reasons"] = [*reasons, "lines cut to fit the response budget"]


register_post_enforce("search_codebase", _lines_after_budget)
