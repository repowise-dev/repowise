"""Derived decision granularity — how much of the codebase a decision spans.

Pure derivation from a record's existing linkage fields (no LLM, no I/O), so
it can run at serialization time for any record, old or new. Levels, narrowest
first: ``file`` < ``module`` < ``cross-module``. ``None`` means the record has
no code linkage at all — better no claim than defaulting the least-grounded
records to the widest level.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["derive_decision_scope"]


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
      none are linked, the top-level directories of the affected files: one →
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
        modules = {f.split("/", 1)[0] for f in files if "/" in f}
        if not modules:
            return "file"
    return "module" if len(modules) == 1 else "cross-module"
