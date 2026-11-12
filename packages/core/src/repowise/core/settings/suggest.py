"""The settings that fit one repository, from facts it already keeps on disk.

A setting that can be suggested carries a ``suggest_when`` predicate (see
:class:`~repowise.core.settings.model.Setting`). :func:`gather_facts` reads a
few small things once: the settings' values, the index scope, what the agent
hooks measured, whether coverage is stored, the tracked decisions and which
embedder is ready. :func:`suggestions` then asks every predicate.

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
    return RepoFacts(
        values=dict(values),
        search=_search(root),
        embedder_found=_embedder_found(root),
        forgone=_forgone(dot / "omissions" / "omissions.db", now - MEASURE_DAYS * 86400),
        agent_sessions=_recent_files(dot / "hook-sessions", now - SESSION_DAYS * 86400),
        # The ingest history, else the coverage rows an older index kept alone.
        coverage_ingested=any(
            _count(wiki, f"SELECT EXISTS (SELECT 1 FROM {table})")
            for table in ("coverage_ingests", "coverage_files")
        ),
        tracked_decisions=_tracked_decisions(root),
    )


def _search(root: Path) -> Mapping[str, Any]:
    from repowise.core.index_scope import load_index_scope

    try:
        scope = load_index_scope(root)
    except Exception:
        return {}
    return (scope or {}).get("search") or {}


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

