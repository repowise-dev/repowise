"""Derived decision granularity — how much of the codebase a decision spans.

Pure derivation from a record's existing linkage fields (no LLM, no I/O), so
it can run at serialization time for any record, old or new. Levels, narrowest
first: ``file`` < ``module`` < ``cross-module``. ``None`` means the record has
no code linkage at all — better no claim than defaulting the least-grounded
records to the widest level.
"""

from __future__ import annotations

from collections.abc import Container, Sequence

from repowise.core.support_paths import file_population
from repowise.core.test_paths import is_test_related_path

__all__ = [
    "MAX_GOVERNING_FILES",
    "SCOPE_BASIS_FOOTPRINT",
    "SCOPE_BASIS_STATED",
    "bind_scope_files",
    "binds_to_paths",
    "commit_scope_basis",
    "commit_scope_files",
    "derive_decision_scope",
    "resolve_module_nodes",
]

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
#: value avoids a third cap to reason about. Also the ceiling on a
#: session-mined record, through :func:`bind_scope_files`: a decision made in
#: one conversation is not about every file that conversation happened to open.
_MAX_FILES = 20


#: The basis value marking a scope that is a commit's footprint rather than a
#: claim about particular files. Stored on the record, read wherever a file is
#: asked what governs it. Any other value (including the empty default) binds.
SCOPE_BASIS_FOOTPRINT = "commit_footprint"

#: The basis value marking a scope a person stated: typed at the CLI, written
#: into the manifest, or confirmed on review. It binds, like the empty default
#: does; the difference is that the backfill repairs an empty basis and never
#: touches this one, so a hand-narrowed scope is not re-marked as a footprint
#: on the next index.
SCOPE_BASIS_STATED = "stated"

#: Above this many files, a commit-derived list stops being a claim about
#: files and becomes the footprint of the change it was mined from. The miner
#: reads one decision out of one commit body and has no per-file evidence, so
#: it takes the commit's whole list: true about the commit, false about most
#: of the files in it.
#:
#: A breadth rule rather than a relevance one because relevance was tried and
#: does not work. Overlap between a decision's text and a file's own diff hunk
#: scores the worst answers highest, since lexical similarity tracks the
#: subsystem a file sits in and not whether the decision governs it.
MAX_GOVERNING_FILES = 10


#: Which population a scope entry is ranked under, narrowest claim first. A
#: decision is about the system, so production code outranks the test that
#: exercises it, which outranks an example, which outranks the docs and
#: config files that describe it.
_POPULATION_RANK = {"production": 0, "test": 1, "example": 2, "doc": 3}


def _normalized(files: Sequence[str] | None) -> list[str]:
    """POSIX-separated, stripped, deduped, in first-seen order."""
    seen: dict[str, None] = {}
    for f in files or []:
        if f and f.strip():
            seen.setdefault(f.replace("\\", "/").strip(), None)
    return list(seen)


def commit_scope_files(files: Sequence[str] | None) -> list[str]:
    """The files a decision mined from one commit may claim to govern.

    Sorted before truncating so which files survive the cap is reproducible,
    rather than depending on the order git happened to list them in.
    """
    return sorted(_normalized(files))[:_MAX_FILES]


def commit_scope_basis(files: Sequence[str] | None) -> str:
    """The scope basis for a record mined from one commit's file list.

    Takes the commit's *whole* list, before :func:`commit_scope_files` caps
    it, so that a large commit stored as a capped list is still a footprint.
    """
    return SCOPE_BASIS_FOOTPRINT if len(_normalized(files)) > MAX_GOVERNING_FILES else ""


def binds_to_paths(scope_basis: str | None) -> bool:
    """Whether a record with this basis may answer "what governs this path".

    The one predicate every path-scoped surface asks, so the wiki pages, the
    ``get_why`` lanes, ``get_context``, the decision graph and the health
    findings agree on which records are specific enough to name a path. A
    record that fails it keeps its files and its place in repository-wide
    answers -- search, the overview, a lookup by id.

    Modules are gated with files. A record's module list is
    :func:`resolve_module_nodes` over the same file list, so it is no better
    evidenced, and gating one without the other would leave the surfaces
    reading this column disagreeing with the ones reading the graph.
    """
    return scope_basis != SCOPE_BASIS_FOOTPRINT


def bind_scope_files(
    files: Sequence[str] | None,
    indexed: Container[str] | None,
) -> list[str]:
    """The entries of *files* the index holds, best claim first.

    *indexed* is the indexed file set — ingestion's ``source_map`` keys, the
    only thing that has applied gitignore, size, binary and generated-file
    rules. Validating against it rather than against the tree is what keeps a
    plan doc, a scratchpad script, or a file belonging to a sibling checkout
    out of a scope: those resolve on disk and are still not this codebase.
    ``None`` means no set was supplied and nothing is filtered, so a caller
    that cannot reach one keeps its previous behaviour. An empty set is the
    same case and callers pass ``None`` for it, because filtering everything
    away is never the right reading of a missing set.

    Ceiling: ``source_map`` is keyed on the files that parsed, so a file that
    was traversed and then failed to read or parse is dropped from a scope
    naming it. That is the whole of the gap — a file with no parser never
    reaches the traverser's own ``FileInfo`` either — and it is worth the
    narrowing, because ``source_map`` is the one set both the full and the
    incremental pipeline already carry to this point.

    Order is the claim quality: population first, then the order the caller
    supplied, which is where a producer that knows which paths were edited
    puts them first. Stable, so both rankings survive together.
    """
    kept = _normalized(files)
    if indexed is not None:
        kept = [f for f in kept if f in indexed]
    ranked = sorted(
        kept,
        key=lambda f: _POPULATION_RANK[file_population(f, is_test=is_test_related_path(f))],
    )
    return ranked[:_MAX_FILES]


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
