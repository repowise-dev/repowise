"""Derived decision granularity — how much of the codebase a decision spans.

Pure derivation from a record's existing linkage fields (no LLM, no I/O), so
it can run at serialization time for any record, old or new. Levels, narrowest
first: ``file`` < ``module`` < ``cross-module``. ``None`` means the record has
no code linkage at all — better no claim than defaulting the least-grounded
records to the widest level.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["commit_scope_files", "derive_decision_scope", "resolve_module_nodes"]

#: Upper bound on the directories one record may claim. A record naming files
#: across more directories than this is not scoped by its module list anyway —
#: its files are the scope — and an unbounded list is what turns a stored
#: column into a second copy of the tree.
_MAX_MODULES = 12

#: Upper bound on the files a commit-derived record may claim. ``git_archaeology``
#: and ``pr`` read one decision out of one commit and then took that commit's
#: whole file list, so a decision mined from a large refactor claimed every file
#: the refactor touched. Measured over the 625-record dev store: scope runs
#: median 5, p75 16, p90 30, max 59, and the 103 records claiming more than 20
#: files (17 ``git_archaeology``, 86 ``pr``) carry 1,468 scope entries beyond
#: it. 20 keeps the p75 record whole and matches the number ``inline_marker``
#: already caps its graph neighbours at — a different quantity, but reusing the
#: value avoids a third cap to reason about.
_MAX_FILES = 20


def commit_scope_files(files: Sequence[str] | None) -> list[str]:
    """The files a decision mined from one commit may claim to govern.

    Sorted before truncating so which files survive the cap is reproducible,
    rather than depending on the order git happened to list them in.
    """
    seen = {f.replace("\\", "/").strip() for f in files or [] if f and f.strip()}
    return sorted(seen)[:_MAX_FILES]


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
