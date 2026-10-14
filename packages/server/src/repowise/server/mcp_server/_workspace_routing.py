"""Route path and symbol targets to the workspace repo that owns them.

Used by ``get_context``, ``get_symbol``, ``get_risk`` and ``get_health`` when
the server runs in workspace mode and the caller gave no ``repo``. Without it
a path from another repo silently answers about the default repo.

Rules, exact matches only:

- ``<alias>:<target>`` names the repo outright (``backend:src/app.py``,
  ``backend:src/app.py::App``). ``component:`` and ``module:`` stay target
  kinds, never repo prefixes.
- A path, or the file part of ``path::Name`` / ``path:120-140``, is served by
  the default repo when it exists there, as before. Otherwise it is routed to
  the one other repo whose checkout has it; when several do, the candidates
  are returned and nothing is guessed.
- A path in no repo falls through to the default repo, whose own not-found
  answer (suggestions, ``moved_to``) is kept; :func:`hint_misses` only adds a
  pointer to ``search_codebase``. In a change set it is a deleted file and
  gets no hint (:func:`settle_missing`).
- Bare names and opaque refs stay with the default repo: resolving them
  elsewhere needs a workspace-wide symbol locator.

Existence is checked on each repo's checkout, so routing opens no database.
Ceiling: one ``exists()`` per non-default repo per missed target; a workspace
locator replaces the probe when the workspace store lands.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from repowise.server.mcp_server import _state
from repowise.server.mcp_server._query_shape import _LINE_SUFFIX_RE, _is_path

_TARGET_KINDS = frozenset({"component", "module"})


@dataclass(frozen=True)
class Route:
    """Where one caller target is served."""

    target: str  # as the caller wrote it
    local: str  # the target inside its repo, any ``alias:`` prefix removed
    repo: str | None  # owning alias; None when unresolved
    routed: bool = False  # True when the repo came from a prefix or a lookup
    candidates: tuple[str, ...] = ()  # set when several repos have the path
    missed: bool = False  # no repo has the path; served by the default repo

    @property
    def refusal(self) -> str | None:
        if self.repo is not None or not self.candidates:
            return None
        listed = ", ".join(self.candidates)
        return (
            f"'{self.target}' exists in several workspace repos ({listed}). "
            f"Name one: '{self.candidates[0]}:{self.local}', or pass repo=."
        )

    def card(self) -> dict[str, Any]:
        """The per-target entry for an unresolved route."""
        entry: dict[str, Any] = {"error": self.refusal}
        if self.candidates:
            entry["candidates"] = list(self.candidates)
        return entry


def route_targets(targets: Sequence[str], *, paths_only: bool = False) -> list[Route] | None:
    """One :class:`Route` per target, or None outside workspace mode.

    ``paths_only`` reads every target without a path shape as a path anyway
    (``get_risk`` and ``get_health`` take only paths, and ``Makefile`` is one).
    """
    registry = _state._registry
    if registry is None:
        return None
    roots = _repo_roots()
    default = registry.get_default_alias()
    return [_route_one(t, roots, default, paths_only) for t in targets]


def needs_routing(routes: list[Route] | None) -> bool:
    """True when any target needs more than the default repo's plain path."""
    return routes is not None and any(r.routed or r.missed or r.repo is None for r in routes)


def single_repo(routes: list[Route]) -> str | None:
    """The one repo every route resolved to, or None."""
    repos = {r.repo for r in routes}
    return next(iter(repos)) if len(repos) == 1 and None not in repos else None


def one_repo_refusal(tool: str, routes: list[Route]) -> dict[str, Any]:
    """For a tool that answers about one repo per call: what each target resolved to.

    Names the per-repo calls when the targets span repos, else the unresolved
    targets' own refusals.
    """
    unresolved = [r for r in routes if r.repo is None]
    if unresolved:
        error = " ".join(r.refusal or "" for r in unresolved)
    else:
        groups = _group(routes)
        calls = "; ".join(
            f'{tool}(targets={[r.local for r in rs]!r}, repo="{alias}")'
            for alias, rs in groups.items()
        )
        error = f"The targets span several workspace repos; {tool} reads one per call: {calls}."
    return {
        "error": error,
        "routes": {
            r.target: {"candidates": list(r.candidates)} if r.candidates else {"repo": r.repo}
            for r in routes
        },
    }


def settle_missing(routes: list[Route], changed: Collection[str]) -> list[Route]:
    """Serve ``changed`` paths no checkout has from the batch's one repo, else the default.

    A change set names deleted files, so for ``changed_files`` absence is not a
    typo; the tool's own history reading answers for them as before. Other
    missing targets keep the default-repo not-found path and its hint.
    """
    registry = _state._registry
    if registry is None:
        return routes
    default = registry.get_default_alias()
    repos = {r.repo for r in routes if r.repo is not None and not r.missed}
    home = next(iter(repos)) if len(repos) == 1 else default
    return [
        replace(r, repo=home, routed=home != default, missed=False)
        if r.missed and r.target in changed
        else r
        for r in routes
    ]


_MISS_HINT = (
    'Not in any workspace repo. Find it with search_codebase(query=..., repo="all"), '
    "then pass '<alias>:<path>'."
)


def hint_misses(response: dict[str, Any], routes: list[Route]) -> None:
    """Add the search hint to a not-found answer for each target no repo has."""
    missed = {r.target for r in routes if r.missed}
    if not missed:
        return
    cards = response.get("targets")
    if isinstance(cards, dict):
        for key, card in cards.items():
            if key in missed and isinstance(card, dict) and (
                card.get("error") or card.get("unresolved_reason")
            ):
                card["hint"] = _MISS_HINT
    for item in response.get("unresolved") or ():
        if isinstance(item, dict) and item.get("target") in missed:
            item["hint"] = _MISS_HINT
    if response.get("error") and not isinstance(cards, dict):
        response["hint"] = _MISS_HINT


def rekey_targets(response: dict[str, Any], routes: list[Route]) -> None:
    """Key the ``targets`` map by what the caller wrote, not the local form."""
    targets = response.get("targets")
    if not isinstance(targets, dict):
        return
    original = {r.local: r.target for r in routes}
    response["targets"] = {original.get(k, k): v for k, v in targets.items()}


async def run_grouped(
    routes: list[Route],
    call: Callable[[str, list[str]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Run ``call(alias, local_targets)`` once per repo and merge the ``targets`` maps.

    Cards keep the caller's target order and key; a routed card carries
    ``repo``. Top-level fields come from the default repo's call (else the
    first), with omission refs pooled so nothing dropped is unrecoverable.
    Sequential on purpose: shared fan-out belongs to one federation helper.
    """
    groups = _group([r for r in routes if r.repo is not None])
    results: dict[str, dict[str, Any]] = {}
    for alias, rs in groups.items():
        results[alias] = await call(alias, list(dict.fromkeys(r.local for r in rs)))

    default = _state._registry.get_default_alias() if _state._registry is not None else None
    lead = results.get(default) if default in results else next(iter(results.values()), {})
    merged: dict[str, Any] = {k: v for k, v in lead.items() if k != "targets"}
    for result in results.values():
        if result is not lead:
            _pool_omissions(merged, result)

    cards: dict[str, Any] = {}
    for r in routes:
        if r.repo is None:
            cards[r.target] = r.card()
            continue
        result = results[r.repo]
        card = (result.get("targets") or {}).get(r.local)
        if card is None:
            card = {"error": result.get("error") or f"No result for '{r.local}'"}
        cards[r.target] = {"repo": r.repo, **card} if r.routed else card
    merged.setdefault("_meta", {})
    response = {"targets": cards, **merged}
    hint_misses(response, routes)
    return response


# -- internals ---------------------------------------------------------------


def _repo_roots() -> dict[str, Path]:
    registry = _state._registry
    if registry is None:
        return {}
    base = Path(registry.workspace_root)
    return {e.alias: Path(os.path.normpath(base / e.path)) for e in registry.ws_config.repos}


def _route_one(target: str, roots: dict[str, Path], default: str, paths_only: bool) -> Route:
    if target.partition(":")[0].casefold() in _TARGET_KINDS:
        return Route(target, target, default)
    alias, local = _split_prefix(target, roots)
    if alias is not None:
        return Route(target, local, alias, routed=True)
    file_part = _LINE_SUFFIX_RE.sub("", target.split("::", 1)[0])
    if not file_part or file_part.startswith("repowise#"):
        return Route(target, target, default)
    if not (paths_only or "::" in target or _is_path(file_part)):
        return Route(target, target, default)

    if Path(file_part).is_absolute():
        return _route_absolute(target, file_part, roots, default)
    if _exists(roots.get(default), file_part):
        return Route(target, target, default)
    owners = tuple(a for a, root in roots.items() if a != default and _exists(root, file_part))
    if len(owners) == 1:
        return Route(target, target, owners[0], routed=True)
    if len(owners) > 1:
        return Route(target, target, None, candidates=owners)
    return Route(target, target, default, missed=True)


def _route_absolute(target: str, file_part: str, roots: dict[str, Path], default: str) -> Route:
    """An absolute path belongs to the repo whose checkout contains it."""
    path = Path(os.path.normpath(file_part))
    # Deepest root first: a nested repo owns its own files.
    for alias, root in sorted(roots.items(), key=lambda kv: -len(kv[1].parts)):
        if not _contains(root, path):
            continue
        if alias == default:
            return Route(target, target, default)
        local = path.relative_to(root).as_posix() + target[len(file_part) :]
        return Route(target, local, alias, routed=True)
    return Route(target, target, default, missed=True)


def _split_prefix(target: str, roots: dict[str, Path]) -> tuple[str | None, str]:
    head, sep, rest = target.partition(":")
    if not sep or not rest or rest.startswith(":") or Path(target).is_absolute():
        return None, target
    for alias in roots:
        if alias.casefold() == head.strip().casefold():
            return alias, rest
    return None, target


def _exists(root: Path | None, rel: str) -> bool:
    if root is None:
        return False
    norm = os.path.normpath(rel)
    if norm == ".." or norm.startswith(".." + os.sep):
        return False
    try:
        return (root / norm).exists()
    except (OSError, ValueError):
        return False


def _contains(root: Path, path: Path) -> bool:
    try:
        rel = os.path.relpath(os.path.normcase(path), os.path.normcase(root))
    except ValueError:  # another drive on Windows
        return False
    return rel == "." or not (rel == ".." or rel.startswith(".." + os.sep))


def _group(routes: list[Route]) -> dict[str, list[Route]]:
    groups: dict[str, list[Route]] = {}
    for r in routes:
        groups.setdefault(r.repo or "", []).append(r)
    return groups


def _pool_omissions(merged: dict[str, Any], other: dict[str, Any]) -> None:
    marker = other.get("omission_marker")
    if marker:
        merged["omission_marker"] = "\n".join(filter(None, [merged.get("omission_marker"), marker]))
    omitted = (other.get("_meta") or {}).get("omitted")
    if not isinstance(omitted, dict):
        return
    meta = merged.setdefault("_meta", {})
    mine = meta.get("omitted")
    if not isinstance(mine, dict):
        meta["omitted"] = omitted
        return
    mine["refs"] = list(dict.fromkeys([*(mine.get("refs") or []), *(omitted.get("refs") or [])]))
