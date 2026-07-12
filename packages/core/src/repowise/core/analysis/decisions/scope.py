"""Derived decision granularity — how much of the codebase a decision spans.

Pure derivation from a record's existing linkage fields (no LLM, no I/O), so
it can run at serialization time for any record, old or new. Levels, narrowest
first: ``function`` < ``file`` < ``module`` < ``cross-module``.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["DECISION_SCOPES", "derive_decision_scope"]

DECISION_SCOPES = ("function", "file", "module", "cross-module")


def derive_decision_scope(
    affected_files: Sequence[str] | None,
    affected_modules: Sequence[str] | None,
    *,
    evidence_file: str | None = None,
    symbol: str | None = None,
) -> str:
    """Return the scope level for one decision record.

    Rules, in order:

    - exactly one affected file (evidence_file counts when the list is empty):
      ``function`` if a *symbol* is identified, else ``file``
    - multiple files, or only modules: one distinct module → ``module``,
      several → ``cross-module``
    - nothing linked at all → ``cross-module`` (the least-specific claim).

    An evidence line narrows nothing on its own — a line without a resolved
    symbol still only proves file-level scope — so it is deliberately not a
    parameter.
    """
    files = {f for f in (affected_files or []) if f}
    if not files and evidence_file:
        files = {evidence_file}
    modules = {m for m in (affected_modules or []) if m}

    if len(files) == 1:
        return "function" if symbol else "file"
    if files:
        return "module" if len(modules) <= 1 else "cross-module"
    if modules:
        return "module" if len(modules) == 1 else "cross-module"
    return "cross-module"
