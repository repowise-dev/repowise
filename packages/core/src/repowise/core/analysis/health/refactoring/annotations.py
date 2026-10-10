"""What the other layers know about a plan's target, attached once at finalize.

Code shape decides whether a plan exists. Decisions, dead code, consumers and
git history only annotate it: a governing decision makes it a judgment call,
and everything else is a ``risks[]`` line a person reads before applying it.
Nothing here creates, removes or hides a plan.

Every read is one bulk query over the whole plan set, made at finalize and
stored with the plan's rank, so plan detail serves it without a query.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....co_change import parse_partners
from ....persistence.crud.analysis.dead_code import get_dead_code_findings
from ....persistence.crud.authority import governing_decisions_by_file
from ....persistence.crud.git import get_git_metadata_bulk
from ....persistence.models import GitCommit, GitFunctionBlame
from ..queue.eligibility import DeadSpan, dead_spans, dead_target
from .extract_helper import ACTIVE_CO_CHANGE
from .models import RefactoringSuggestion

RiskKind = Literal["decision", "public_api", "active_edit", "dead_code"]

#: Commits on the target inside this many days before the newest indexed
#: commit read as someone working on it now.
ACTIVE_EDIT_DAYS = 14
ACTIVE_EDIT_MIN_COMMITS = 2
#: Stored history must reach back this many windows, or a shallow clone's
#: boundary commit and a capped walk read as recent activity everywhere.
_HISTORY_WINDOWS = 2
MAX_CO_CHANGE_PARTNERS = 5
_MAX_DECISION_RISKS = 3
#: Kinds that move a symbol out of its file, so its consumers see the change.
_RELOCATING = frozenset({"split_file", "extract_class", "move_method"})
#: The blast-radius key each file-level builder writes its dependents under.
_DEPENDENTS_KEY = {"split_file": "dependent_count", "extract_class": "dependents_count"}
_BATCH = 500

FunctionCommits = tuple[int, int, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class PlanRisk:
    kind: RiskKind
    text: str
    ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "ref": self.ref}


@dataclass(frozen=True, slots=True)
class PlanAnnotations:
    """``governed_by`` decision ids, ``risks``, and co-change partners as
    ``(file, commits shared)``, strongest first."""

    governed_by: tuple[str, ...] = ()
    risks: tuple[PlanRisk, ...] = ()
    co_change_partners: tuple[tuple[str, int], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Only the non-empty parts; ``{}`` still says the plan was checked."""
        out: dict[str, Any] = {}
        if self.governed_by:
            out["governed_by"] = list(self.governed_by)
        if self.risks:
            out["risks"] = [risk.as_dict() for risk in self.risks]
        if self.co_change_partners:
            out["co_change_partners"] = partner_rows(self.co_change_partners)
        return out

    @classmethod
    def from_dict(cls, stored: Any) -> PlanAnnotations | None:
        """``None`` when nothing was stored: the plan was never checked."""
        if not isinstance(stored, Mapping):
            return None
        risks = tuple(
            PlanRisk(row["kind"], row["text"], row.get("ref"))
            for row in stored.get("risks") or ()
            if isinstance(row, Mapping) and row.get("kind") and row.get("text")
        )
        partners = tuple(
            (row["file_path"], int(row.get("commits") or 0))
            for row in stored.get("co_change_partners") or ()
            if isinstance(row, Mapping) and isinstance(row.get("file_path"), str)
        )
        governed = tuple(str(item) for item in stored.get("governed_by") or ())
        return cls(governed, risks, partners)


def partner_rows(partners: Sequence[tuple[str, int]]) -> list[dict[str, Any]]:
    return [{"file_path": path, "commits": commits} for path, commits in partners]


@dataclass(frozen=True, slots=True)
class AnnotationFacts:
    """The repository facts every plan's annotations are read from.

    ``decisions`` maps each file a governing decision names to ``(id, title)``
    pairs. ``recent`` is each commit inside the active-edit window, ``sha ->
    (committed_at, author)``; ``None`` when history is too shallow to tell
    recent from all, which is no signal rather than a quiet target. ``dead``
    is the sure dead-code spans by file (:func:`..queue.eligibility.dead_spans`).
    """

    decisions: Mapping[str, list[tuple[str, str]]] = field(default_factory=dict)
    partners: Mapping[str, tuple[tuple[str, int], ...]] = field(default_factory=dict)
    functions: Mapping[str, list[FunctionCommits]] = field(default_factory=dict)
    recent: Mapping[str, tuple[datetime, str]] | None = None
    dead: Mapping[str, list[DeadSpan]] = field(default_factory=dict)


def annotate(suggestion: RefactoringSuggestion, facts: AnnotationFacts) -> PlanAnnotations:
    """*suggestion*'s annotations from *facts*. Pure."""
    decisions = list(facts.decisions.get(suggestion.file_path, ()))
    risks = [
        PlanRisk(
            "decision",
            f'Decision "{title}" governs this file; check it allows this change first.',
            decision_id,
        )
        for decision_id, title in decisions[:_MAX_DECISION_RISKS]
    ]
    for risk in (
        _dead_code_risk(suggestion, facts.dead),
        _public_api_risk(suggestion),
        _active_edit_risk(suggestion, facts),
    ):
        if risk is not None:
            risks.append(risk)
    return PlanAnnotations(
        governed_by=tuple(decision_id for decision_id, _ in decisions),
        risks=tuple(risks),
        co_change_partners=facts.partners.get(suggestion.file_path, ()),
    )


def _dead_code_risk(
    suggestion: RefactoringSuggestion, dead: Mapping[str, list[DeadSpan]]
) -> PlanRisk | None:
    """A target a sure dead-code finding covers, by the rule Fix first excludes it with."""
    if not dead_target(
        dead, suggestion.file_path, suggestion.target_symbol or None, suggestion.line_start
    ):
        return None
    what = f"`{suggestion.target_symbol}`" if suggestion.target_symbol else "this file"
    return PlanRisk("dead_code", f"Nothing reaches {what}; delete it instead of refactoring it.")


def _public_api_risk(suggestion: RefactoringSuggestion) -> PlanRisk | None:
    """Consumers of a symbol the plan moves out of its file, from its own blast radius."""
    kind = suggestion.refactoring_type
    if kind not in _RELOCATING:
        return None
    blast = suggestion.blast_radius or {}
    if kind == "move_method":
        callers = _count(blast.get("callers"))
        method = suggestion.target_symbol.rsplit(".", 1)[-1]
        if not callers or method.startswith("_"):
            return None
        to_class = (suggestion.plan or {}).get("to_class") or "its new class"
        return PlanRisk(
            "public_api",
            f"`{suggestion.target_symbol}` has {callers} caller{_s(callers)}; each has to "
            f"reach it through `{to_class}` after the move.",
        )
    dependents = _count(blast.get(_DEPENDENTS_KEY[kind]))
    if not dependents:
        return None
    files = "1 file" if dependents == 1 else f"{dependents} files"
    if kind == "split_file":
        verb = "imports" if dependents == 1 else "import"
        text = f"{files} {verb} from this file; update them unless it re-exports what moves."
    else:
        verb = "depends" if dependents == 1 else "depend"
        text = f"{files} {verb} on this file; the class they use changes shape."
    return PlanRisk("public_api", text)


def _active_edit_risk(suggestion: RefactoringSuggestion, facts: AnnotationFacts) -> PlanRisk | None:
    """Recent commits on the functions the plan's lines sit in. Function blame is
    the finest stored grain, so a span inside a long function counts all of it."""
    recent = facts.recent
    if recent is None:
        return None
    shas = {
        sha
        for _start, _end, commits in _target_functions(suggestion, facts.functions)
        for sha in commits
        if sha in recent
    }
    if len(shas) < ACTIVE_EDIT_MIN_COMMITS:
        return None
    latest = max(shas, key=lambda sha: recent[sha][0])
    authors = sorted({recent[sha][1] for sha in shas})
    who = authors[0] if len(authors) == 1 else f"{len(authors)} authors"
    return PlanRisk(
        "active_edit",
        f"Changed in {len(shas)} commits by {who} in the {ACTIVE_EDIT_DAYS} days before "
        "the last index; check nobody is mid-change.",
        latest,
    )


def _target_functions(
    suggestion: RefactoringSuggestion, functions: Mapping[str, list[FunctionCommits]]
) -> list[FunctionCommits]:
    """The innermost function holding the plan's lines, else every function they
    overlap; the whole file for a plan with no lines."""
    rows = functions.get(suggestion.file_path, [])
    if not suggestion.line_start:
        return rows
    start, end = suggestion.line_start, suggestion.line_end or suggestion.line_start
    overlapping = [row for row in rows if row[0] <= end and row[1] >= start]
    enclosing = [row for row in overlapping if row[0] <= start and end <= row[1]]
    return [min(enclosing, key=lambda row: row[1] - row[0])] if enclosing else overlapping


def _count(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _s(count: int) -> str:
    return "" if count == 1 else "s"


async def load_annotation_facts(
    session: AsyncSession, repository_id: str, suggestions: Sequence[RefactoringSuggestion]
) -> AnnotationFacts:
    """Every fact :func:`annotate` reads, for the whole plan set at once."""
    files = sorted({item.file_path for item in suggestions if item.file_path})
    metadata = await get_git_metadata_bulk(session, repository_id, files)
    dead = await get_dead_code_findings(session, repository_id)
    recent = await _recent_commits(session, repository_id)
    return AnnotationFacts(
        decisions=await governing_decisions_by_file(session, repository_id),
        partners={path: _partners(meta) for path, meta in metadata.items()},
        functions=await _function_commits(session, repository_id, files) if recent else {},
        recent=recent,
        dead=dead_spans(dead),
    )


def _partners(meta: Any) -> tuple[tuple[str, int], ...]:
    """Files that changed with this one often enough to update together."""
    strong = sorted(
        (
            (partner.file_path, partner.support)
            for partner in parse_partners(getattr(meta, "co_change_partners_json", None))
            if partner.support >= ACTIVE_CO_CHANGE
        ),
        key=lambda pair: (-pair[1], pair[0]),
    )
    return tuple(strong[:MAX_CO_CHANGE_PARTNERS])


def _json_list(raw: Any) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


async def _recent_commits(
    session: AsyncSession, repository_id: str
) -> dict[str, tuple[datetime, str]] | None:
    """Commits in the window before the newest indexed one, or ``None`` when
    stored history does not reach back far enough to tell.

    The guard measures the span of the stored ``git_commits`` rows, which a
    commit cap can shorten; a short span reads as no signal, never as quiet.
    A shallow clone's boundary commit, which blame credits with every older
    line, is the oldest stored row (or, capped, not stored at all), so the
    span guard already keeps it two windows clear of this one.
    """
    newest, oldest = (
        await session.execute(
            select(func.max(GitCommit.committed_at), func.min(GitCommit.committed_at)).where(
                GitCommit.repository_id == repository_id
            )
        )
    ).one()
    window = timedelta(days=ACTIVE_EDIT_DAYS)
    if newest is None or oldest is None or newest - oldest < window * _HISTORY_WINDOWS:
        return None
    rows = await session.execute(
        select(GitCommit.sha, GitCommit.committed_at, GitCommit.author_name).where(
            GitCommit.repository_id == repository_id,
            GitCommit.committed_at >= newest - window,
        )
    )
    return {sha: (when, author or "unknown") for sha, when, author in rows}


async def _function_commits(
    session: AsyncSession, repository_id: str, files: Sequence[str]
) -> dict[str, list[FunctionCommits]]:
    """Each function's blame commits, for the plans' files."""
    blame = GitFunctionBlame
    out: dict[str, list[FunctionCommits]] = {}
    for index in range(0, len(files), _BATCH):
        rows = await session.execute(
            select(blame.file_path, blame.start_line, blame.end_line, blame.commit_shas_json).where(
                blame.repository_id == repository_id,
                blame.file_path.in_(list(files[index : index + _BATCH])),
                blame.commit_shas_json.is_not(None),
            )
        )
        for path, start, end, shas in rows:
            out.setdefault(path, []).append((int(start or 0), int(end or 0), tuple(_json_list(shas))))
    return out


__all__ = [
    "ACTIVE_EDIT_DAYS",
    "ACTIVE_EDIT_MIN_COMMITS",
    "MAX_CO_CHANGE_PARTNERS",
    "AnnotationFacts",
    "PlanAnnotations",
    "PlanRisk",
    "RiskKind",
    "annotate",
    "load_annotation_facts",
    "partner_rows",
]
