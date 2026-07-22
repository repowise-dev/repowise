"""Cascade policy for scoped generation.

Regenerating a file page makes the pages that summarize it drift: its module
page, the cycle (SCC) pages it belongs to, its architectural layer page, and
the repo-wide overview / architecture / onboarding pages. Backlinks on other
pages that point at it also go stale. The healer ``backfill_related_pages``
repairs backlinks LLM-free after any run; this module decides what to do about
the *content* of the dependent pages.

Three policies, chosen by the user:

* ``none`` — generate exactly what was asked; mark every structural dependent
  stale (truthful, free, instant).
* ``dependents`` — also regenerate the module, SCC and layer pages that
  contain a seeded file; mark the repo-wide pages stale.
* ``full`` — also regenerate the repo-wide pages. Nothing is left stale.

The dependent set is computed structurally from the selection graph and KG
layer membership, so it is exact rather than a heuristic cascade.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from .models import compute_page_id

CascadeMode = Literal["none", "dependents", "full"]

_FILE_PAGE_PREFIX = "file_page:"


@dataclass(frozen=True)
class PageDependencies:
    """Which container pages each file page rolls up into.

    Built once per run from the in-memory selection and KG layers. All values
    are page ids (``module_page:...``, ``scc_page:...``, ``layer_page:...``),
    ready to drop straight into an ``only_page_ids`` set.
    """

    module_page_of: dict[str, str] = field(default_factory=dict)
    scc_pages_of: dict[str, tuple[str, ...]] = field(default_factory=dict)
    layer_page_of: dict[str, str] = field(default_factory=dict)
    repo_wide_ids: tuple[str, ...] = ()

    def containers_of(self, file_path: str) -> set[str]:
        """Module + SCC + layer page ids that summarize *file_path*."""
        ids: set[str] = set()
        mod = self.module_page_of.get(file_path)
        if mod:
            ids.add(mod)
        ids.update(self.scc_pages_of.get(file_path, ()))
        layer = self.layer_page_of.get(file_path)
        if layer:
            ids.add(layer)
        return ids


@dataclass(frozen=True)
class CascadeResult:
    """Outcome of expanding a seed set under a cascade mode.

    ``generate_ids`` is the full ``only_page_ids`` set the engine should emit;
    ``stale_ids`` are dependents left unregenerated that the caller should mark
    stale (never overlaps ``generate_ids``).
    """

    generate_ids: set[str]
    stale_ids: set[str]


def build_page_dependencies(
    *,
    module_groups: Iterable[Any],
    scc_groups: Iterable[tuple[str, Iterable[str]]],
    layer_page_of: dict[str, str] | None = None,
    repo_wide_ids: Iterable[str] = (),
) -> PageDependencies:
    """Invert the selection groups into per-file container page ids.

    ``module_groups`` are duck-typed selection ``ModuleGroup`` objects (``.key``
    + ``.file_paths``); ``scc_groups`` are ``(scc_id, files)`` pairs, exactly as
    :class:`~repowise.core.generation.selection.Selection` carries them.
    ``layer_page_of`` maps a file path to its ``layer_page:<id>`` (from KG layer
    membership; empty when the repo has no KG).
    """
    module_page_of: dict[str, str] = {}
    for group in module_groups:
        page_id = compute_page_id("module_page", group.key)
        for path in group.file_paths:
            module_page_of.setdefault(path, page_id)

    scc_pages_of: dict[str, list[str]] = {}
    for scc_id, files in scc_groups:
        page_id = compute_page_id("scc_page", scc_id)
        for path in files:
            scc_pages_of.setdefault(path, []).append(page_id)

    return PageDependencies(
        module_page_of=module_page_of,
        scc_pages_of={p: tuple(v) for p, v in scc_pages_of.items()},
        layer_page_of=dict(layer_page_of or {}),
        repo_wide_ids=tuple(repo_wide_ids),
    )


def _seed_file_paths(seed_ids: set[str]) -> set[str]:
    """Repo-relative paths of the file-page seeds (others carry no file cascade)."""
    return {pid[len(_FILE_PAGE_PREFIX) :] for pid in seed_ids if pid.startswith(_FILE_PAGE_PREFIX)}


def expand_cascade(
    seed_ids: set[str],
    mode: CascadeMode,
    deps: PageDependencies,
) -> CascadeResult:
    """Expand *seed_ids* into the pages to generate and the pages to mark stale.

    The container set (module/SCC/layer pages of the seeded files) and the
    repo-wide set are the two rings of fallout. ``none`` regenerates neither and
    marks both stale; ``dependents`` regenerates the containers and marks
    repo-wide stale; ``full`` regenerates both and marks nothing.
    """
    seed_files = _seed_file_paths(seed_ids)
    containers: set[str] = set()
    for path in seed_files:
        containers.update(deps.containers_of(path))
    repo_wide = set(deps.repo_wide_ids)

    if mode == "full":
        generate = seed_ids | containers | repo_wide
        stale: set[str] = set()
    elif mode == "dependents":
        generate = seed_ids | containers
        stale = repo_wide
    else:  # "none"
        generate = set(seed_ids)
        stale = containers | repo_wide

    # A page is never both regenerated and marked stale; regeneration wins.
    stale -= generate
    return CascadeResult(generate_ids=generate, stale_ids=stale)
