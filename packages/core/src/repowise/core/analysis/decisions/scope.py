"""Derived decision granularity — how much of the codebase a decision spans.

Pure derivation from a record's existing linkage fields (no LLM, no I/O), so
it can run at serialization time for any record, old or new. Levels, narrowest
first: ``file`` < ``module`` < ``cross-module``. ``None`` means the record has
no code linkage at all — better no claim than defaulting the least-grounded
records to the widest level.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["derive_decision_scope", "resolve_module_nodes"]

#: Upper bound on the directories one record may claim. A record naming files
#: across more directories than this is not scoped by its module list anyway —
#: its files are the scope — and an unbounded list is what turns a stored
#: column into a second copy of the tree.
_MAX_MODULES = 12


def resolve_module_nodes(files: Sequence[str] | None) -> list[str]:
    """Return the directories that *files* live in, deduped and sorted.

    The one convention for a record's module linkage. Two disagreeing ones
    were in use before: the first path segment, which in a packages/ layout
    yields ``packages`` or ``tests`` for essentially every record, and the
    containing directory. This is the second, because it is the only one that
    is real by construction — a directory holding a file the record names
    exists whether or not a graph was built, so no caller needs one.

    Root-level files contribute nothing: a record naming ``README.md`` is
    scoped by that file, not by the repository.
    """
    seen: dict[str, None] = {}
    for f in files or []:
        if not f:
            continue
        norm = f.replace("\\", "/").strip("/")
        parent, sep, _ = norm.rpartition("/")
        if sep and parent:
            seen.setdefault(parent, None)
    return sorted(seen)[:_MAX_MODULES]


def derive_decision_scope(
    affected_files: Sequence[str] | None,
    affected_modules: Sequence[str] | None,
    *,
    evidence_file: str | None = None,
) -> str | None:
    """Return the scope level for one decision record, or ``None``.

    Rules, in order:

    - nothing linked at all: ``file`` when an *evidence_file* pins the record
      to one file, else ``None`` — explicit file/module linkage always
      outranks the evidence-file fallback
    - exactly one affected file → ``file``
    - otherwise count distinct modules — the explicitly linked ones, or when
      none are linked, the directories the affected files live in: one →
      ``module``, several → ``cross-module``; multiple root-level files with
      no directory stay ``file``.

    An evidence line narrows nothing on its own — a line without a resolved
    symbol still only proves file-level scope — so it is deliberately not a
    parameter.
    """
    files = {f for f in (affected_files or []) if f}
    modules = {m for m in (affected_modules or []) if m}

    if not files and not modules:
        return "file" if evidence_file else None
    if len(files) == 1:
        return "file"
    if not modules:
        # Same convention as resolve_module_nodes, so a record scored before
        # its modules were backfilled reports the granularity it will keep.
        modules = set(resolve_module_nodes(sorted(files)))
        if not modules:
            return "file"
    return "module" if len(modules) == 1 else "cross-module"
