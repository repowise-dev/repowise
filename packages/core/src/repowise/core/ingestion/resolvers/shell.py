"""Shell ``source`` / ``.`` import resolution.

A sourced path reaches this resolver after the parser has stripped the
surrounding quotes (see ``resolvers/luau.py`` for the same contract), so
``source "$SCRIPT_DIR/lib/util.sh"`` arrives as ``$SCRIPT_DIR/lib/util.sh``.

Handled forms:

1. Literal relative path — ``./lib/util.sh``, ``lib/util.sh``, ``../x.sh`` —
   resolved against the *sourcing* file's directory.
2. Script-directory idioms — a leading variable/command-substitution segment
   that evaluates to the script's own directory is stripped and the remainder
   resolved relatively::

       $SCRIPT_DIR/x.sh
       $(dirname "$0")/x.sh
       ${BASH_SOURCE%/*}/x.sh
       `dirname "$0"`/x.sh

3. Project-root idioms — one or more leading variable segments that point at a
   project root rather than the script's dir::

       $BATS_ROOT/$BATS_LIBDIR/bats-core/warnings.bash

   After stripping every leading variable segment, the literal tail
   (``bats-core/warnings.bash``) is matched against repo paths by *unique*
   multi-segment suffix. The uniqueness + multi-segment guards keep this
   precise — a bare ``$X/common.sh`` (single-segment tail) is never linked.
4. Anything still carrying interpolation, an absolute system path, or a
   ``~`` home path is recorded as an external node so the reference still
   appears in the graph, never silently matched.

Unresolved literals are *not* guessed by bare filename — a wrong source edge is
worse than no edge for a graph that feeds docs and dead-code detection.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ResolverContext

_SHELL_SUFFIXES: tuple[str, ...] = (".sh", ".bash", ".zsh")

# A single leading directory-anchor segment: ``${...}``, ``$(...)``,
# `` `...` ``, or ``$NAME`` — the part that evaluates to a directory in the
# idioms above. Matched only at the very start of the path.
_DIR_ANCHOR = re.compile(r"^(?:\$\{[^}]*\}|\$\([^)]*\)|`[^`]*`|\$\w+)/")

# Any remaining shell interpolation after prefix-stripping — such a path can't
# be resolved statically.
_DYNAMIC = re.compile(r"[$`]")


def resolve_shell_import(
    module_path: str,
    importer_path: str,
    ctx: ResolverContext,
) -> str | None:
    """Resolve a ``source``/``.`` argument to a repo-relative file path."""
    raw = module_path.strip()
    if not raw:
        return None

    # Strip every leading directory-anchor segment ($SCRIPT_DIR/, $(dirname
    # "$0")/, ${BASH_SOURCE%/*}/, and multi-var $BATS_ROOT/$LIBDIR/…).
    tail = raw
    stripped = 0
    while True:
        shorter = _DIR_ANCHOR.sub("", tail, count=1)
        if shorter == tail:
            break
        tail = shorter
        stripped += 1

    # Interior interpolation, a home path, or an absolute system path — none
    # resolvable to a repo file.
    if _DYNAMIC.search(tail) or tail.startswith(("~", "/")):
        return ctx.add_external_node(raw)

    importer_dir = posixpath.dirname(importer_path)

    # Literal-relative (no anchor) or the script-dir idiom (anchor == the
    # sourcing file's own directory): resolve the tail relative to the importer.
    resolved = _resolve_relative(tail, importer_dir, ctx)
    if resolved is not None:
        return resolved

    # Project-root idiom: the anchor was not the script's dir, so relative
    # resolution missed. Fall back to a UNIQUE multi-segment path-suffix match
    # — a single bare filename is too ambiguous to link safely.
    if stripped and "/" in tail:
        match = _unique_suffix_match(tail, ctx)
        if match is not None:
            return match

    return ctx.add_external_node(raw)


def _candidate_paths(tail: str) -> tuple[str, ...]:
    """The tail plus shell-suffixed variants when the extension is omitted."""
    if any(tail.endswith(s) for s in _SHELL_SUFFIXES):
        return (tail,)
    return (tail, *(f"{tail}{s}" for s in _SHELL_SUFFIXES))


def _resolve_relative(tail: str, importer_dir: str, ctx: ResolverContext) -> str | None:
    """Resolve *tail* against the importing file's directory."""
    for cand in _candidate_paths(tail):
        resolved = posixpath.normpath(posixpath.join(importer_dir, cand))
        if resolved in ctx.path_set:
            return resolved
    return None


def _unique_suffix_match(tail: str, ctx: ResolverContext) -> str | None:
    """Return the single repo path ending in ``/<tail>`` (or ``tail``), else None.

    Precision guard: only an unambiguous (exactly one) match links. A tie
    resolves to no edge rather than a guessed one.
    """
    for cand in _candidate_paths(tail):
        needle = f"/{cand}"
        hits = [p for p in ctx.sorted_paths if p == cand or p.endswith(needle)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    return None
