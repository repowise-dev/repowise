"""Versioned content identity for refactoring plans.

A plan's public id is derived from what the plan *is*, never from where the row
landed in storage. Two consecutive analyses of unchanged source must produce the
same string, so an agent that quoted an id yesterday can still resolve it, and a
dismissal recorded against it still suppresses the same plan.

The kernel is therefore built per refactoring type out of that type's stable
anchors - symbol names, clone content, group membership - and never out of line
numbers, prose, ranking, effort or confidence. Moving a function down a file
must not mint a new plan; changing what the plan asks you to do must.

The kernel's inputs are a contract. Adding a fact must not change them; changing
them means bumping :data:`REFACTORING_MODEL_VERSION`, which makes every stored id
self-describing as stale rather than silently wrong.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..rows import field

REFACTORING_MODEL_VERSION = 2
"""Version of the identity semantics. v1 was the fresh-UUID-per-index era.

The version is the id prefix rather than a hash input, so a stale id is
recognisable from the string alone. Moving this constant means bumping
``HEALTH_ANALYZER_VERSION`` with it, which forces the reanalysis that restamps
every stored plan.
"""

RefactoringKernel = tuple[Any, ...]

_ID_PREFIX = "refac"
_ID_PATTERN = re.compile(rf"^{_ID_PREFIX}(\d*)_[0-9a-f]{{20}}$")

_DIGEST_CHARS = 20


def _plan_of(suggestion: Any) -> dict:
    """The plan payload, whether this is a dataclass, a dict or an ORM row."""
    plan = getattr(suggestion, "plan", None)
    if plan is None and isinstance(suggestion, dict):
        plan = suggestion.get("plan")
    if plan is None:
        raw = getattr(suggestion, "plan_json", None)
        if raw is None and isinstance(suggestion, dict):
            raw = suggestion.get("plan_json")
        try:
            plan = json.loads(raw or "{}")
        except (TypeError, ValueError):
            plan = {}
    return plan if isinstance(plan, dict) else {}


def _evidence_of(suggestion: Any) -> dict:
    evidence = getattr(suggestion, "evidence", None)
    if evidence is None and isinstance(suggestion, dict):
        evidence = suggestion.get("evidence")
    if evidence is None:
        raw = getattr(suggestion, "evidence_json", None)
        if raw is None and isinstance(suggestion, dict):
            raw = suggestion.get("evidence_json")
        try:
            evidence = json.loads(raw or "{}")
        except (TypeError, ValueError):
            evidence = {}
    return evidence if isinstance(evidence, dict) else {}


def _span_length(suggestion: Any) -> int | None:
    """Span size, which survives the whole block moving down the file."""
    start = field(suggestion, "line_start")
    end = field(suggestion, "line_end")
    if isinstance(start, int) and isinstance(end, int):
        return end - start
    return None


def _text_digest(text: str) -> str:
    """Content digest of a source block, insensitive to trailing whitespace."""
    normalized = "\n".join(line.rstrip() for line in text.splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _clone_kernel(suggestion: Any) -> RefactoringKernel:
    """A clone group is named by the duplicated block and where it occurs.

    Deliberately excludes ``file_path``: that column names the anchor
    occurrence, and the anchor moves to the smallest surviving site whenever a
    sibling occurrence is filtered out. The occurrence set is the identity.
    """
    plan = _plan_of(suggestion)
    evidence = _evidence_of(suggestion)
    occurrences = plan.get("occurrences") or []
    files = sorted(
        str(item.get("file", "")) for item in occurrences if isinstance(item, dict)
    )
    snippet = plan.get("snippet")
    return (
        "extract_helper",
        _text_digest(snippet) if isinstance(snippet, str) else None,
        tuple(files),
        len(occurrences),
        evidence.get("token_count"),
        plan.get("duplicated_lines"),
    )


def _cycle_kernel(suggestion: Any) -> RefactoringKernel:
    """A cycle is named by its members and the cut, not by a representative file."""
    plan = _plan_of(suggestion)
    cycle = plan.get("cycle") or []
    cuts = plan.get("cut_edges") or []
    return (
        "break_cycle",
        tuple(sorted(str(member) for member in cycle)),
        tuple(
            sorted(
                (str(edge.get("from", "")), str(edge.get("to", "")))
                for edge in cuts
                if isinstance(edge, dict)
            )
        ),
    )


def _extract_class_kernel(suggestion: Any, file_path: str) -> RefactoringKernel:
    """A class split is named by the class and by the clusters it proposes.

    The class name alone would hold still while the proposed split changed under
    it, so an id an agent quoted for one extraction would answer with another.
    """
    plan = _plan_of(suggestion)
    groups = plan.get("groups") or []
    membership = sorted(
        (
            tuple(sorted(str(name) for name in (group.get("methods") or []))),
            tuple(sorted(str(name) for name in (group.get("fields") or []))),
        )
        for group in groups
        if isinstance(group, dict)
    )
    return (
        "extract_class",
        file_path,
        field(suggestion, "target_symbol") or "",
        tuple(membership),
    )


def _split_kernel(suggestion: Any, file_path: str) -> RefactoringKernel:
    """A split is named by its group membership; suggested filenames are advice."""
    plan = _plan_of(suggestion)
    groups = plan.get("groups") or []
    membership = sorted(
        tuple(sorted(str(symbol) for symbol in (group.get("symbols") or [])))
        for group in groups
        if isinstance(group, dict)
    )
    return ("split_file", file_path, tuple(membership))


def _extract_method_kernel(suggestion: Any, file_path: str) -> RefactoringKernel:
    """An extraction is named by its host function and the signature it lifts.

    The span itself is line-based, so only its length participates. Two spans in
    one function that agree on signature and length collide; :func:`assign_public_ids`
    breaks that tie deterministically rather than letting the ids merge.
    """
    plan = _plan_of(suggestion)
    return (
        "extract_method",
        file_path,
        field(suggestion, "target_symbol") or "",
        tuple(str(name) for name in (plan.get("params") or [])),
        tuple(str(name) for name in (plan.get("returns") or [])),
        _span_length(suggestion),
    )


def _move_method_kernel(suggestion: Any, file_path: str) -> RefactoringKernel:
    plan = _plan_of(suggestion)
    return (
        "move_method",
        file_path,
        str(plan.get("method") or ""),
        str(plan.get("from_class") or ""),
        str(plan.get("to_class") or ""),
    )


def _generic_kernel(kind: str, suggestion: Any, file_path: str) -> RefactoringKernel:
    """The degrade path for a type with no declared anchor.

    Deterministic and content-derived, but span-length sensitive, so a future
    detector that lands here without its own branch gets a working id rather
    than a wrong one. New types should add a branch.
    """
    return (
        kind,
        file_path,
        field(suggestion, "target_symbol") or "",
        _span_length(suggestion),
    )


def refactoring_kernel(suggestion: Any) -> RefactoringKernel:
    """The identity kernel for one plan.

    Accepts a detector dataclass, a plain dict, or a persisted ORM row: the
    three describe the same plan and must reach the same string.
    """
    kind = str(field(suggestion, "refactoring_type") or "")
    file_path = str(field(suggestion, "file_path") or "")
    biomarker = str(field(suggestion, "source_biomarker") or "")

    if kind == "performance_fix":
        # The performance layer already owns a versioned causal id for this
        # plan and persists it in the plan payload; deriving a second one here
        # would let the two disagree about the same row.
        return ("performance_fix", str(_plan_of(suggestion).get("opportunity_id") or ""))
    if kind == "extract_helper":
        return _clone_kernel(suggestion)
    if kind == "break_cycle":
        return _cycle_kernel(suggestion)
    if kind == "split_file":
        return (*_split_kernel(suggestion, file_path), biomarker)
    if kind == "extract_method":
        return (*_extract_method_kernel(suggestion, file_path), biomarker)
    if kind == "move_method":
        return (*_move_method_kernel(suggestion, file_path), biomarker)
    if kind == "extract_class":
        return (*_extract_class_kernel(suggestion, file_path), biomarker)
    return (*_generic_kernel(kind, suggestion, file_path), biomarker)


def stable_id(kernel: RefactoringKernel, *, ordinal: int = 0) -> str:
    """Hash a kernel into the public id.

    *ordinal* disambiguates plans whose kernels genuinely coincide; it is 0 for
    the overwhelming majority and never appears in the string.
    """
    payload = json.dumps(
        [kernel, ordinal] if ordinal else kernel,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
    return f"{_ID_PREFIX}{REFACTORING_MODEL_VERSION}_{digest}"


def refactoring_public_id(suggestion: Any) -> str:
    """The public id for one plan, with no collision context available.

    Single-row callers (a plan written on its own, a lookup rebuilding an id to
    compare) use this. Batch writers use :func:`assign_public_ids`, which can
    see collisions and break them.
    """
    return stable_id(refactoring_kernel(suggestion))


def assign_public_ids(suggestions: list[Any]) -> list[str]:
    """Public ids for a whole batch, positionally aligned with *suggestions*.

    Kernels that coincide are ordered by their coordinates and given ascending
    ordinals, so the assignment is a function of the batch's content rather than
    of iteration order. A uniform line shift preserves that order, so the ids
    hold; adding or removing a colliding sibling renumbers the ones after it,
    which is the honest outcome of the group having changed.
    """
    kernels = [refactoring_kernel(item) for item in suggestions]
    positions: dict[RefactoringKernel, list[int]] = {}
    for index, kernel in enumerate(kernels):
        positions.setdefault(kernel, []).append(index)

    ids: list[str] = [""] * len(suggestions)
    for kernel, members in positions.items():
        if len(members) == 1:
            ids[members[0]] = stable_id(kernel)
            continue
        ordered = sorted(
            members,
            key=lambda index: (
                str(field(suggestions[index], "file_path") or ""),
                field(suggestions[index], "line_start") or 0,
                str(field(suggestions[index], "target_symbol") or ""),
            ),
        )
        for ordinal, index in enumerate(ordered):
            ids[index] = stable_id(kernel, ordinal=ordinal)
    return ids


def public_id_model_version(public_id: str) -> int | None:
    """Which model minted this id, or nothing if it was not minted here."""
    match = _ID_PATTERN.match(public_id or "")
    if match is None:
        return None
    return int(match.group(1)) if match.group(1) else 1


def model_state(public_id: str) -> dict[str, Any]:
    """Whether an id can still be resolved, and what to do when it cannot.

    Ids are not translated across models: a kernel change can split or merge
    what an older id named, and an alias would have to invent which one the
    caller meant. Naming the mismatch and the refresh that fixes it is the only
    honest answer.
    """
    version = public_id_model_version(public_id)
    if version == REFACTORING_MODEL_VERSION:
        state = "current"
    elif version is None:
        state = "unrecognized"
    else:
        state = "stale_model"
    return {
        "state": state,
        "public_id": public_id,
        "requested_model_version": version,
        "refactoring_model_version": REFACTORING_MODEL_VERSION,
        "refresh_required": state == "stale_model",
    }
