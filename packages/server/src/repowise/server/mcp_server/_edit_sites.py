"""Every site an agent edits to rename one symbol or update all its callers.

The graph says where to look (the definition, importers of its file, call
edges with their lines); the live files say what is there now. Each candidate
file is scanned for the symbol's bare name on a word boundary and every hit is
classified, so a stale graph line is dropped instead of served, and a hit the
graph never saw is still listed. ``complete`` is true only when nothing the
scan could not see may hold another site.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from itertools import islice
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.fs_walk import PRUNED_DIRS, iter_glob
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.symbol_identity import id_segment_name
from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.core.test_paths import is_test_related_path
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._helpers import read_repo_file_text
from repowise.server.mcp_server._meta import _working_tree_dirty_paths, uncommitted_targets

MAX_SITES = 200
MAX_TEXT_CHARS = 160
# Plain mentions kept per file when a collector can hold the rest: a mock-heavy
# test can name a symbol hundreds of times and bury its real callers.
MAX_REFERENCES_PER_FILE = 3
# Ceiling on files read per call; past it the set is reported incomplete
# rather than read without bound.
MAX_FILES = 300
_MAX_FILE_BYTES = 2_000_000
_MIN_CALL_CONFIDENCE = 0.7
_MEMBER_KINDS = frozenset({"method", "constructor", "property", "field"})
# Languages where using a symbol from another file takes an import naming that
# file or the symbol, so importers are every file that can use it. Elsewhere
# (same-package or namespace visibility) sites are listed but never complete.
_IMPORT_SCOPED_LANGUAGES = frozenset({"python", "typescript", "javascript"})
_PYTHON_EXTENSIONS = REGISTRY.extensions_for(("python",))
# A statement that brings a name into a file, in the languages the graph
# resolves imports for. Continuation lines are found by bracket balance.
_IMPORT_START = re.compile(
    r"^\s*(?:from\s+\S+\s+import\b|import\b|export\s.*\bfrom\b|export\s*\{|using\b|use\b"
    r"|#\s*include\b|(?:const|let|var)\s.*\brequire\s*\()"
)


def _bare_name(node: GraphNode) -> str:
    return id_segment_name((node.name or node.node_id).split("::")[-1].split(".")[-1])


def _import_lines(lines: list[str]) -> set[int]:
    """1-based line numbers that belong to an import statement."""
    out: set[int] = set()
    i = 0
    while i < len(lines):
        if not _IMPORT_START.match(lines[i]):
            i += 1
            continue
        depth = 0
        while i < len(lines):
            text = lines[i]
            out.add(i + 1)
            depth += text.count("(") + text.count("{") - text.count(")") - text.count("}")
            i += 1
            if depth <= 0 and not text.rstrip().endswith("\\"):
                break
    return out


def _read_lines(root: Path, rel: str) -> list[str] | None:
    try:
        if (root / rel).stat().st_size > _MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    text = read_repo_file_text(root, rel)
    return None if text is None else text.splitlines()


def _scan(
    root: Path,
    files: list[str],
    name: str,
    definition: tuple[str, int, int],
    call_lines: dict[str, set[int]],
) -> tuple[list[dict[str, Any]], list[str], int, set[str], bool]:
    """``(sites, unreadable, stale call lines, files renaming it, default-exported)``."""
    esc = re.escape(name)
    word = re.compile(rf"(?<![\w$]){esc}(?![\w$])")
    as_rename = re.compile(rf"(?<![\w$.]){esc}\s+as\s+([\w$]+)")
    # ``{ name: other } = require(...)`` or a destructured import; a ternary's
    # ``name : other`` is excluded, anything else colon-shaped counts.
    colon_rename = re.compile(rf"(?<![\w$.?]){esc}\s*:\s*([A-Za-z_$][\w$]*)")
    ternary = re.compile(rf"\?[^:]*(?<![\w$]){esc}\s*:")
    # Bound under any name by its importers, so no spelling can be scanned for.
    default_export = re.compile(r"\bexport\s+default\b|\bas\s+default\b")
    cjs_export = re.compile(r"\bmodule\.exports\b|(?<![\w$.])exports\.")
    def_path, def_start, def_end = definition
    sites: list[dict[str, Any]] = []
    unreadable: list[str] = []
    stale = 0
    renamed: set[str] = set()
    is_default = False
    for rel in files:
        lines = _read_lines(root, rel)
        if lines is None:
            unreadable.append(rel)
            continue
        imports = _import_lines(lines)
        python = Path(rel).suffix in _PYTHON_EXTENSIONS
        aliases: set[str] = set()
        for n, text in enumerate(lines, 1):
            if not word.search(text):
                continue
            if n in imports:
                aliases.update(m.group(1) for m in as_rename.finditer(text))
            if not python and not ternary.search(text):
                aliases.update(m.group(1) for m in colon_rename.finditer(text))
        # ``name as default`` is the default-export case below, not a rename.
        aliases.discard("default")
        if aliases:
            renamed.add(rel)
        if rel == def_path and not python:
            # A CommonJS module's exports can rename on any line, so any use counts.
            commonjs = any(cjs_export.search(t) for t in lines)
            if commonjs or any(default_export.search(t) and word.search(t) for t in lines):
                is_default = True
        spellings = [word] + [re.compile(rf"(?<![\w$]){re.escape(a)}(?![\w$])") for a in aliases]
        graph_calls = call_lines.get(rel, set())
        def_seen = rel != def_path
        for n, text in enumerate(lines, 1):
            in_graph = n in graph_calls
            if in_graph and not any(s.search(text) for s in spellings):
                stale += 1
                continue
            if not in_graph and not any(s.search(text) for s in spellings):
                continue
            if not def_seen and def_start <= n <= def_end:
                kind, def_seen = "definition", True
            elif n in imports:
                kind = "import"
            elif in_graph:
                kind = "call"
            else:
                kind = "reference"
            sites.append({"path": rel, "line": n, "kind": kind, "text": text.strip()[:MAX_TEXT_CHARS]})
    return sites, unreadable, stale, renamed, is_default


def _cap_references_per_file(sites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*sites* with plain references past the per-file cap dropped; other kinds always stay."""
    seen: Counter[str] = Counter()
    kept: list[dict[str, Any]] = []
    for site in sites:
        if site["kind"] == "reference":
            seen[site["path"]] += 1
            if seen[site["path"]] > MAX_REFERENCES_PER_FILE:
                continue
        kept.append(site)
    return kept


def _unseen_dirty_files(root: Path, local_path: str) -> list[str] | None:
    """Uncommitted files the index has not seen; ``None`` when git cannot say."""
    dirty = _working_tree_dirty_paths(local_path)
    if dirty is None:
        return None
    candidates = [d for d in dirty if d.split("/", 1)[0] not in PRUNED_DIRS]
    out: list[str] = []
    for rel in uncommitted_targets(local_path, candidates):
        path = root / rel
        if path.is_dir():
            found = (f for f in iter_glob(path, "*") if f.is_file())
            out.extend(f.relative_to(root).as_posix() for f in islice(found, MAX_FILES + 1))
        elif path.is_file():
            out.append(rel)
    return out


async def reference_edit_set(
    session: AsyncSession,
    repo_id: str,
    repo_root: str | Path,
    node: GraphNode,
    collector: OmissionCollector | None = None,
) -> dict[str, Any]:
    """Every live site naming *node*, classified, with a completeness verdict.

    Returns ``{"sites": [{"path", "line", "kind", "text"}], "complete": bool}``
    plus ``reasons`` when incomplete and ``sites_omitted`` past the cap. Kinds:
    definition | import | call | reference. With a *collector*, plain
    references past ``MAX_REFERENCES_PER_FILE`` in one file move to it and
    ``sites_omitted_by_file`` counts every row not shown.
    """
    root = Path(repo_root).resolve()
    name = _bare_name(node)
    def_file = node.file_path
    reasons: list[str] = []

    cols = (
        GraphEdge.source_node_id,
        GraphEdge.target_node_id,
        GraphEdge.edge_type,
        GraphEdge.imported_names_json,
        GraphEdge.call_lines_json,
        GraphEdge.confidence,
    )

    def _names(raw: str | None) -> list[str]:
        try:
            return [str(n) for n in json.loads(raw or "[]")]
        except (TypeError, ValueError):
            return []

    edges = (
        await session.execute(
            select(*cols).where(
                GraphEdge.repository_id == repo_id,
                or_(
                    GraphEdge.target_node_id == node.node_id,
                    (GraphEdge.target_node_id == def_file)
                    & (GraphEdge.edge_type == "dynamic_uses"),
                ),
            )
        )
    ).all()

    # Importers of the definition file, then anything re-exporting the name
    # onward from them, so a package barrel's own importers are scanned too.
    files: set[str] = {def_file}
    files.update(row[0] for row in edges if row[2] == "imports")
    frontier, seen = [def_file], {def_file}
    while frontier and len(files) <= MAX_FILES:
        rows = (
            await session.execute(
                select(GraphEdge.source_node_id, GraphEdge.imported_names_json).where(
                    GraphEdge.repository_id == repo_id,
                    GraphEdge.edge_type == "imports",
                    GraphEdge.target_node_id.in_(frontier),
                )
            )
        ).all()
        frontier = []
        for src, names_json in rows:
            files.add(src)
            names = _names(names_json)
            # Unknown names may carry this one onward, so they are followed too.
            if src not in seen and (not names or name in names or "*" in names):
                seen.add(src)
                frontier.append(src)

    call_lines: dict[str, set[int]] = {}
    uncertain = dynamic = unlined = False
    for src, tgt, etype, names_json, lines_json, conf in edges:
        if etype == "dynamic_uses":
            names = _names(names_json)
            if tgt == node.node_id or not names or name in names:
                dynamic = True
            continue
        if tgt != node.node_id or etype in ("imports", "defines"):
            continue
        src_file = src.split("::", 1)[0]
        lines = {int(n) for n in _names(lines_json) if n.isdigit()}
        if etype == "calls" and not lines:
            unlined = True
        if not lines:
            continue
        files.add(src_file)
        call_lines.setdefault(src_file, set()).update(lines)
        if (conf or 0) < _MIN_CALL_CONFIDENCE:
            uncertain = True

    unseen = await asyncio.to_thread(_unseen_dirty_files, root, str(repo_root))
    if unseen is None:
        reasons.append("working tree status unknown")
        unseen = []
    unindexed = sorted(set(unseen) - files)

    ordered = sorted(files)
    if len(ordered) + len(unindexed) > MAX_FILES:
        reasons.append(f"over {MAX_FILES} candidate files")
        ordered = ordered[:MAX_FILES]
        unindexed = unindexed[: max(0, MAX_FILES - len(ordered))]
    definition = (def_file, node.start_line or 0, node.end_line or node.start_line or 0)
    sites, unreadable, stale, renamed_in, is_default = await asyncio.to_thread(
        _scan, root, ordered + unindexed, name, definition, call_lines
    )

    unindexed_set = set(unindexed)
    if unreadable:
        reasons.append(f"unreadable: {', '.join(unreadable[:3])}")
    if stale:
        reasons.append(f"stale: {stale} graph call lines no longer name it")
    if unlined:
        reasons.append("call edges without line numbers")
    if uncertain:
        reasons.append("low-confidence call edges")
    if dynamic:
        reasons.append("dynamic use of the symbol or its file")
    language = node.language or await session.scalar(
        select(GraphNode.language).where(
            GraphNode.repository_id == repo_id, GraphNode.node_id == def_file
        )
    )
    language = (language or "").lower()
    if language not in _IMPORT_SCOPED_LANGUAGES:
        reasons.append(f"{language or 'unknown language'}: same-package use is not scanned")
    if renamed_in:
        reasons.append(f"renamed on import or export: {', '.join(sorted(renamed_in)[:3])}")
    if is_default:
        reasons.append("default export: importers may rename it")
    if node.kind in _MEMBER_KINDS or node.node_id.count("::") > 1:
        reasons.append("member: calls through untyped receivers are not scanned")
    touched = sorted({s["path"] for s in sites if s["path"] in unindexed_set})
    if touched:
        reasons.append(f"unindexed edits name it: {', '.join(touched[:3])}")
    if not any(s["kind"] == "definition" for s in sites) and def_file not in unreadable:
        reasons.append("definition not found at its indexed line")

    sites.sort(
        key=lambda s: (
            s["kind"] != "definition",
            is_test_related_path(s["path"]),
            s["path"],
            s["line"],
        )
    )
    kept = _cap_references_per_file(sites) if collector is not None else sites
    if len(kept) > MAX_SITES:
        reasons.append(f"over {MAX_SITES} sites")
    if len(kept) < len(sites):
        reasons.append(
            f"{len(sites) - len(kept)} references past {MAX_REFERENCES_PER_FILE} per file"
            " not shown: sites_omitted_by_file; the full list is under _meta.omitted"
        )
    out: dict[str, Any] = {"sites": kept[:MAX_SITES], "complete": not reasons}
    if collector is None:
        if len(sites) > MAX_SITES:
            out["sites_omitted"] = len(sites) - MAX_SITES
    elif len(out["sites"]) < len(sites):
        shown = {id(s) for s in out["sites"]}
        cap_collection(
            out,
            "sites",
            sites,
            MAX_SITES,
            collector,
            emitted=out["sites"],
            label=f"{node.node_id} :: references.sites not shown",
            reason="per_file_reference_cap" if len(kept) < len(sites) else "construction_cap",
        )
        out["sites_omitted_by_file"] = dict(
            Counter(s["path"] for s in sites if id(s) not in shown)
        )
    if reasons:
        out["reasons"] = reasons
    return out
