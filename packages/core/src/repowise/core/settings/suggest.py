"""The settings that fit one repository, from facts it already keeps on disk.

A setting that can be suggested carries a ``suggest_when`` predicate (see
:class:`~repowise.core.settings.model.Setting`). :func:`gather_facts` reads a
few small things once: the settings' values, the index scope, what the agent
hooks measured, whether coverage is stored, the tracked decisions, which
embedder is ready, how much git history and how many file pages the index
holds, and which directories hold only vendored or generated files.
:func:`suggestions` then asks every predicate.

Nothing is written and no model is called. A read that fails reads as no
evidence, so an unreadable file means fewer suggestions, never a wrong one.
A repository whose settings already fit gets none.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repowise.core.settings import registry
from repowise.core.settings.model import SCHEMA_VERSION, Benefit, Resolved, Setting, Source

__all__ = ["MEASURE_DAYS", "SESSION_DAYS", "RepoFacts", "Suggestion", "gather_facts", "suggestions"]

#: How far back the hook measurements count.
MEASURE_DAYS = 30
#: How far back an agent session counts as recent. The hooks sweep their
#: per-session files after a week, so a longer window would read the same.
SESSION_DAYS = 7


@dataclass(frozen=True, slots=True)
class RepoFacts:
    """What the predicates may read. Every field defaults to "no evidence"."""

    #: The effective value of each setting that has a predicate, by key.
    values: Mapping[str, Any] = field(default_factory=dict)
    #: The index scope's search leg: ``full_text`` and ``semantic`` states.
    search: Mapping[str, Any] = field(default_factory=dict)
    #: ``(name, why)`` of a real embedder ready on this machine, else ``None``.
    embedder_found: tuple[str, str] | None = None
    #: Per replacing hook surface that is off: ``(items, tokens)`` it would
    #: have saved over :attr:`measure_days`.
    forgone: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    measure_days: int = MEASURE_DAYS
    #: Agent sessions whose repowise hooks ran here in the last :attr:`session_days`.
    agent_sessions: int = 0
    session_days: int = SESSION_DAYS
    coverage_ingested: bool = False
    #: Active decisions in ``.repowise/decisions.yaml``.
    tracked_decisions: int = 0
    #: The last full git pass's ``git_history_coverage`` (see
    #: :class:`~repowise.core.ingestion.git_indexer.records.GitHistoryCoverage`).
    git_history: Mapping[str, Any] = field(default_factory=dict)
    #: Non-merge commits reachable from HEAD when the repository was indexed.
    total_commits: int = 0
    #: The index scope's ``file_pages`` figures and how the pages were written.
    file_pages: Mapping[str, Any] = field(default_factory=dict)
    content_provenance: str = "unknown"
    #: Production files (by stored ``code_origin``) with no file page, read
    #: only when the scope says file pages were left out.
    unpaged_production: int = 0
    #: Files with a stored ``code_origin``, and the largest directories in
    #: which every one of them is vendored or generated: ``(dir, files)``.
    analysed_files: int = 0
    copied_dirs: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One setting worth changing here, and the command that changes it."""

    key: str
    current: Any
    value: Any
    reason: str
    impact: str
    benefit: Benefit
    command: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "key": self.key,
            "current_value": self.current,
            "suggested_value": self.value,
            "reason": self.reason,
            "impact": self.impact,
            "benefit": self.benefit,
            "command": self.command,
        }


def suggestions(root: Path | str) -> list[Suggestion]:
    """Every setting whose predicate holds for *root*, in registry order.

    A setting an environment variable decides is left alone: the variable is
    a deliberate choice for this shell, and a file write would not move it.
    """
    root = Path(root)
    settings = [s for s in registry.all_settings(include_internal=False) if s.suggest_when]
    resolved = _resolve(root, settings)
    if resolved is None:
        return []
    facts = gather_facts(root, {key: r.value for key, r in resolved.items()})
    found = (_suggestion(s, resolved[s.key], facts) for s in settings)
    return [s for s in found if s is not None]


def _suggestion(s: Setting, current: Resolved, facts: RepoFacts) -> Suggestion | None:
    """*s*'s suggestion, unless the environment decides it or it already holds."""
    predicate = s.suggest_when
    if predicate is None or current.source is Source.ENV or current.locked:
        return None
    try:
        proposal = predicate(facts)
    except Exception:  # a predicate's bug costs its suggestion, never the command
        return None
    if proposal is None or proposal.value == current.value:
        return None
    return Suggestion(
        key=s.key,
        current=current.value,
        value=proposal.value,
        reason=proposal.reason,
        impact=proposal.impact,
        benefit=proposal.benefit,
        command=proposal.command or _set_command(s, proposal.value),
    )


def _resolve(root: Path, settings: list[Setting]) -> dict[str, Resolved] | None:
    """Each setting's value, reading each file once; ``None`` when one is unreadable."""
    from repowise.core.settings.writers import current, read_config

    files: dict[str | None, dict[str, Any]] = {}
    out: dict[str, Resolved] = {}
    try:
        for s in settings:
            if s.path not in files:
                files[s.path] = read_config(root, s)
            out[s.key] = current(root, s, config=files[s.path])
    except Exception:
        return None
    return out


def _set_command(s: Setting, value: Any) -> str:
    text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return f"repowise config set {s.key} {text}"


def gather_facts(root: Path | str, values: Mapping[str, Any]) -> RepoFacts:
    """The facts about *root* the predicates read, each from one small read."""
    root = Path(root).resolve()
    dot = root / ".repowise"
    now = time.time()
    wiki = dot / "wiki.db"
    scope = _scope(root)
    file_pages = scope.get("file_pages") or {}
    analysed, copied = _copied_dirs(wiki)
    return RepoFacts(
        values=dict(values),
        search=scope.get("search") or {},
        embedder_found=_embedder_found(root),
        forgone=_forgone(dot / "omissions" / "omissions.db", now - MEASURE_DAYS * 86400),
        agent_sessions=_recent_files(dot / "hook-sessions", now - SESSION_DAYS * 86400),
        # The ingest history, else the coverage rows an older index kept alone.
        coverage_ingested=any(
            _count(wiki, f"SELECT EXISTS (SELECT 1 FROM {table})")
            for table in ("coverage_ingests", "coverage_files")
        ),
        tracked_decisions=_tracked_decisions(root),
        git_history=scope.get("git_history_coverage") or {},
        total_commits=_total_commits(wiki),
        file_pages=file_pages,
        content_provenance=str(scope.get("content_provenance") or "unknown"),
        # The join is the one read here that grows with the wiki, so it waits
        # for the scope to say some file pages were left out.
        unpaged_production=_count(wiki, _UNPAGED_PRODUCTION_SQL)
        if (file_pages.get("omitted") or 0) > 0
        else 0,
        analysed_files=analysed,
        copied_dirs=copied,
    )


def _scope(root: Path) -> Mapping[str, Any]:
    from repowise.core.index_scope import load_index_scope

    try:
        return load_index_scope(root) or {}
    except Exception:
        return {}


def _total_commits(wiki: Path) -> int:
    """The stored whole-history commit count, when the store holds one repository."""
    rows = _query(wiki, "SELECT total_commit_count FROM repositories")
    return int(rows[0][0] or 0) if len(rows) == 1 else 0


_UNPAGED_PRODUCTION_SQL = (
    "SELECT COUNT(*) FROM health_file_metrics WHERE code_origin = 'production' "
    "AND file_path NOT IN (SELECT target_path FROM wiki_pages WHERE page_type = 'file_page')"
)
#: Origins of code nobody here edits: the ones an exclude pattern is for.
_COPIED_ORIGINS = ("vendored", "generated")
_COPIED_COUNT_SQL = (
    "SELECT COUNT(*), SUM(code_origin IN ('vendored', 'generated')) FROM health_file_metrics"
)
_ORIGINS_SQL = "SELECT file_path, code_origin FROM health_file_metrics"
#: A directory holding fewer copied files than this costs the index little.
MIN_COPIED_DIR_FILES = 200


def _copied_dirs(wiki: Path) -> tuple[int, tuple[tuple[str, int], ...]]:
    """Analysed files, and the top directories holding only vendored or generated ones.

    Reads the stored ``code_origin``, never a fresh guess. The per-path walk
    runs only when copied files are a sizeable share of the store at all.
    """
    rows = _query(wiki, _COPIED_COUNT_SQL)
    total, copied = (int(rows[0][0] or 0), int(rows[0][1] or 0)) if rows else (0, 0)
    if copied < MIN_COPIED_DIR_FILES:
        return total, ()
    counts = _folder_counts(_query(wiki, _ORIGINS_SQL))
    whole = {d for d, (n, c) in counts.items() if c == n and n >= MIN_COPIED_DIR_FILES}
    top = [d for d in whole if not any(d.startswith(f"{p}/") for p in whole)]
    found = sorted(((d, counts[d][0]) for d in top), key=lambda item: (-item[1], item[0]))
    return total, tuple(found)


def _folder_counts(rows: list[tuple[Any, ...]]) -> dict[str, list[int]]:
    """``{folder: [files, copied files]}`` under every folder, from ``(path, origin)`` rows.

    Counts per parent folder first, then carries each count up its ancestors:
    one step per file plus one per folder level, not one per file and level.
    """
    direct: dict[str, list[int]] = {}
    for path, origin in rows:
        counts = direct.setdefault(str(path).rpartition("/")[0], [0, 0])
        counts[0] += 1
        counts[1] += origin in _COPIED_ORIGINS
    out: dict[str, list[int]] = {}
    for folder, (files, copied) in direct.items():
        parts = folder.split("/") if folder else []
        for depth in range(1, len(parts) + 1):
            counts = out.setdefault("/".join(parts[:depth]), [0, 0])
            counts[0] += files
            counts[1] += copied
    return out


def _embedder_found(root: Path) -> tuple[str, str] | None:
    """The embedder ``reindex`` would pick ignoring the repo's pin, when it is a real one."""
    from repowise.core.providers.embedding.resolve import embedder_reason, resolve_embedder_name

    try:
        name, source = resolve_embedder_name(repo_path=root, use_pin=False)
        if name == "mock":
            return None
        return name, embedder_reason(name, source, repo_path=root)
    except Exception:
        return None


def _query(db: Path, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Rows from a read-only open of *db*; ``[]`` when it is absent or unreadable."""
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=0.2)
    except sqlite3.Error:
        return []
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _count(db: Path, sql: str) -> int:
    rows = _query(db, sql)
    return int(rows[0][0] or 0) if rows else 0


def _recent_files(directory: Path, since: float) -> int:
    """Files in *directory* written since *since*; one per agent session for the hooks."""
    try:
        return sum(1 for f in directory.iterdir() if f.stat().st_mtime >= since)
    except OSError:
        return 0


def _forgone(db: Path, since: float) -> dict[str, tuple[int, int]]:
    """Per hook surface: distinct items and tokens it would have saved since *since*."""
    rows = _query(
        db,
        "SELECT filter, COUNT(DISTINCT path), SUM(raw_tokens - distilled_tokens) "
        "FROM forgone_savings WHERE created_at >= ? AND filter != '' GROUP BY filter",
        (since,),
    )
    return {str(name): (int(items), int(tokens or 0)) for name, items, tokens in rows}


def _tracked_decisions(root: Path) -> int:
    from repowise.core.analysis.decisions.manifest import load_manifest

    try:
        return sum(d.currency == "active" for d in load_manifest(root)[0])
    except Exception:
        return 0

