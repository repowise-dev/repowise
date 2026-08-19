"""Who *may* be an execution entry point — the shared candidacy rule.

Candidacy and ordering are two questions. This module owns the first: given a
path and its language, can this file be where a reader enters the system at
all? Ordering — which of the survivors comes first — lives in
:mod:`repowise.core.generation.entry_points`, which reads the rules here.

It sits at the ``core`` layer beside :mod:`repowise.core.test_paths`, for the
reason that one gives: the question is asked during ingestion *and* downstream,
so it cannot live under ``ingestion/`` without generation and analysis
importing across a layer to reach it. **The decision is made once, at
ingestion** — :func:`not_an_execution_start` is what stops
``FileInfo.is_entry_point`` being set on a name-derived guess the file's
language or position contradicts. The knowledge-graph curator and the tour
scorer call it again on stored rows, so a re-derivation and the stored flag can
never disagree: they are the same code.

Nothing here touches a DB, a graph or an LLM — the language registry is a pure
data table — so every rule is unit-testable directly.
"""

from __future__ import annotations

from functools import cache
from pathlib import PurePosixPath

# Generic module stems that *dispatch or re-export* rather than start a program.
# ``index`` (a JS/TS barrel or a per-language resolver shell) and ``mod`` (a Rust
# module root) gather siblings; they are glue, not a control-flow front door.
# Distinct from the registry's broader generic-entry set (main/app/server/cli/…),
# which are genuine execution starts.
GLUE_STEMS: frozenset[str] = frozenset({"index", "mod"})

# A glue-stem file is only plausibly a real entry when it sits at or very near a
# package root. Buried deeper, it is a dispatch/re-export leaf (a resolver's
# ``index.py`` nested under ``ingestion/resolvers/dotnet/``), never where a
# reader enters the system.
SHALLOW_ENTRY_DEPTH = 1

# Language tags recorded by indexes written before language normalisation.
# None is a registry spec tag, so on a current index every member is inert
# (measured: 0 flagged files carry one across 42 indexes); they matter only for
# surfaces reading a stored ``language`` string off an older index.
_NON_CODE_ALIASES: frozenset[str] = frozenset(
    {"md", "yml", "txt", "html", "css", "csv", "xml", "svg", "rst", "text", "ini", "cmake"}
)


@cache
def conventional_entry_stems() -> frozenset[str]:
    """Filename stems that name a real execution start.

    Registry-derived, so a language that adds an entry stem is covered here
    too, minus the glue stems above so ``index``/``mod`` cannot be both at
    once.

    Resolved on first use, not at import, for the reason
    :func:`repowise.core.test_paths.is_test_path` gives: the registry lives
    under ``ingestion/``, and importing anything from there runs
    ``ingestion/__init__``, which imports the traverser, which imports this
    module.
    """
    from .ingestion.languages.registry import REGISTRY

    return REGISTRY.entry_filename_stems() - GLUE_STEMS


@cache
def non_code_entry_languages() -> frozenset[str]:
    """Languages that describe or wire the system rather than start it.

    Config/markup/data plus infra: a ``server.json`` or a ``Dockerfile`` is
    never where execution starts, however entry-like its name.
    """
    from .ingestion.languages.registry import REGISTRY

    return REGISTRY.config_languages() | REGISTRY.infra_languages() | _NON_CODE_ALIASES


def entry_point_depth(path: str) -> int:
    """Directory depth — 0 for a root file, 1 for one level deep, etc."""
    return max(0, len(PurePosixPath(path).parts) - 1)


def is_glue_leaf(path: str) -> bool:
    """True for a generic-glue stem nested below the shallow band.

    These define real symbols (so ``_is_barrel`` keeps them) yet are
    dispatch/re-export leaves, not execution entry points — excluded from the
    orientation entry-point list and never seeded as a tour entry point.
    """
    return (
        PurePosixPath(path).stem.lower() in GLUE_STEMS
        and entry_point_depth(path) > SHALLOW_ENTRY_DEPTH
    )


def not_an_execution_start(path: str, language: str) -> bool:
    """True for a file that cannot be where a reader enters the system.

    Config/data files (``server.json``) describe the system and deep
    generic-glue leaves (a resolver's ``index.py``) dispatch within it —
    neither is an execution start.

    **Not the whole candidacy rule.** The knowledge-graph curator and the tour
    scorer both drop adjacent layers and support paths beside this call, and
    the curator drops re-export barrels; those need a layer map or parsed
    files, so they stay where their inputs are. This owns the two conditions
    that need only a path and a language, which is what lets ingestion apply
    them too.
    """
    return language in non_code_entry_languages() or is_glue_leaf(path)
