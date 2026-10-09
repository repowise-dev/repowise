"""The source lines a search for one identifier or literal names.

An identifier that resolves to one to three indexed symbols gets their edit
sets (definition, imports, calls, references) from :mod:`_edit_sites`. A
single-token query naming no indexed symbol (a number, a quoted string, an
unindexed name) is scanned for in every file git lists (tracked and
untracked, ignore rules applied), so config and docs the indexer never parsed
are read too. ``complete`` is true only when nothing was skipped or capped,
so the lines are every match.
"""

from __future__ import annotations

import asyncio
import codecs
import logging
import os
import re
import subprocess
import time
from collections.abc import Container
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.fs_walk import PRUNED_DIRS, walk_repo
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode
from repowise.core.repo_config import load_repo_config
from repowise.server.mcp_server._budget import register_post_enforce
from repowise.server.mcp_server._edit_sites import (
    _MAX_FILE_BYTES,
    MAX_TEXT_CHARS,
    reference_edit_set,
)
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    is_excluded,
    read_repo_file_text,
)
from repowise.server.mcp_server._query_shape import (
    _canonical_symbol_query,
    _identifier_candidates,
    _qual_norm,
)
from repowise.server.mcp_server._symbol_lookup import symbol_id_variants

_log = logging.getLogger("repowise.mcp.search")

MAX_LINES = 50
MAX_LINES_PER_FILE = 5
MAX_SYMBOLS = 3
MAX_SCAN_FILES = 20_000
MAX_SCAN_BYTES = 32_000_000
# Wall-clock ceiling on the literal scan; past it the lines are marked incomplete.
MAX_SCAN_SECONDS = 0.5
_SNIFF_BYTES = 8192
# A binary past this is not streamed for the token; it is reported as skipped.
_MAX_STREAM_BYTES = 64_000_000
OVER_LINES = f"over {MAX_LINES} lines"
_KIND_ORDER = {"definition": 0, "import": 1, "call": 2, "reference": 3, "match": 4}
_QUOTED = re.compile(r"""^(["'`])(.+)\1$""")
_TOKEN = re.compile(r"^[A-Za-z0-9_$][\w$.]*$")
_DECL_WORDS = r"def|class|function|const|let|var|fn|func|type|interface|struct|enum"


def _literal(query: str) -> tuple[str, bool] | None:
    """``(needle, word_bounded)`` for a single-token query, else ``None``."""
    q = query.strip()
    if m := _QUOTED.match(q):
        return m.group(2), False
    if len(q) >= 2 and _TOKEN.match(q) and not q.endswith("."):
        return q, True
    return None


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
    """``(line number, line)`` for up to one past the per-file cap."""
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
        if len(out) > MAX_LINES_PER_FILE:
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


def _listed_files(root: Path) -> list[str]:
    """Tracked and untracked files minus ignored ones, or a pruned walk without git."""
    cmd = ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        proc = None
    if proc is not None and proc.returncode == 0:
        listed = proc.stdout.decode("utf-8", errors="replace").split("\0")
        return sorted(
            {p for p in listed if p and not PRUNED_DIRS.intersection(p.split("/")[:-1])}
        )
    return sorted(
        (Path(d) / f).relative_to(root).as_posix() for d, _, names in walk_repo(root) for f in names
    )


def _scan_listed(root: Path, needle: str, word: bool) -> tuple[list[dict[str, Any]], list[str]]:
    files = _listed_files(root)
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
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    undecodable = binary_hits = 0
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
                        f"over {MAX_SCAN_BYTES // 1_000_000} MB: scanned {i} of {len(files)} files"
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
        if len(found) > MAX_LINES_PER_FILE:
            per_file_capped += 1
            del found[MAX_LINES_PER_FILE:]
        hits = [(n, line, "definition" if definition.search(line) else "match") for n, line in found]
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
        rows += [
            {"path": rel, "line": n, "kind": k, "text": line.strip()[:MAX_TEXT_CHARS]}
            for n, line, k in hits
        ]
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
        reasons.append(f"over {MAX_LINES_PER_FILE} lines in {per_file_capped} files")
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
    if repo == "all" or mode == "path":
        return
    literal = _literal(query)
    candidates = _identifier_candidates(query, mode, names)
    if not candidates and literal and literal[1]:
        candidates = [literal[0]]
    if not candidates and not literal:
        return
    ctx = await _resolve_repo_context(repo)
    spec = _get_exclude_spec(ctx.path)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        root = Path(repository.local_path or ctx.path)
        nodes = []
        if candidates:
            found = await _exact_symbols(session, repository.id, query, candidates)
            nodes = [n for n in found if not is_excluded(n.file_path, spec)]
        if len(nodes) > MAX_SYMBOLS:
            response.update(
                lines=[], complete=False, reasons=[f"{len(nodes)} symbols match; narrow the query"]
            )
            return
        sets = [await reference_edit_set(session, repository.id, root, n) for n in nodes]
    if sets:
        rows, reasons = _merge(sets)
    elif literal:
        rows, reasons = await asyncio.to_thread(_scan_listed, root, *literal)
    else:
        return
    rows.sort(key=lambda r: (_KIND_ORDER[r["kind"]], r["path"], r["line"]))
    if len(rows) > MAX_LINES and OVER_LINES not in reasons:
        reasons.append(OVER_LINES)
    response["lines"] = rows[:MAX_LINES]
    response["complete"] = not reasons
    if reasons:
        response["reasons"] = reasons


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
