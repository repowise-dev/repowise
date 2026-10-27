"""Route path and symbol targets to the workspace repo that owns them.

Used by ``get_context``, ``get_symbol``, ``get_risk`` and ``get_health`` when
the server runs in workspace mode and the caller gave no ``repo``. Without it
a path from another repo silently answers about the default repo.

Rules, exact matches only:

- ``<alias>:<target>`` names the repo outright (``backend:src/app.py``,
  ``backend:src/app.py::App``). ``component:``, ``change:``, ``module:`` and
  ``package:`` stay target kinds, never repo prefixes.
- A path, or the file part of ``path::Name`` / ``path:120-140``, is served by
  the default repo when it exists there, as before. Otherwise it is routed to
  the one other repo whose checkout has it; when several do, the candidates
  are returned and nothing is guessed.
- A path in no repo falls through to the default repo, whose own not-found
  answer (suggestions, ``moved_to``) is kept; :func:`hint_misses` only adds a
  pointer to ``search_codebase``. In a change set it is a deleted file and
  gets no hint (:func:`settle_missing`).
- A bare identifier (``get_answer``, ``Client.send``) is looked up in the
  workspace store's symbol locator, by exact name or qualified name. The
  default repo keeps it when it has the symbol, a component of that name or
  a path of that name; otherwise it routes to the one other repo that has it,
  and several give candidates. A name no repo has, or one that is not
  identifier-shaped, stays with the default repo.
- Omission refs (``repowise#<hex>``) stay with the default repo here;
  ``get_symbol`` looks them up in every repo's omission store.

Path existence is checked on each repo's checkout, so path routing opens no
database. Ceiling: one ``exists()`` per non-default repo per missed path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import Any

from repowise.server.mcp_server import _state
from repowise.server.mcp_server._federation import Federation
from repowise.server.mcp_server._query_shape import _LINE_SUFFIX_RE, _is_path

_log = logging.getLogger("repowise.mcp")

_TARGET_KINDS = frozenset({"component", "change", "module", "package"})
# A dotted identifier: the only bare targets the symbol locator routes.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*")


@dataclass(frozen=True)
class Route:
    """Where one caller target is served."""

    target: str  # as the caller wrote it
    local: str  # the target inside its repo, any ``alias:`` prefix removed
    repo: str | None  # owning alias; None when unresolved
    routed: bool = False  # True when the repo came from a prefix or a lookup
    candidates: tuple[str, ...] = ()  # set when several repos have the path or name
    missed: bool = False  # no repo has the path; served by the default repo
    name: bool = False  # a bare symbol name, still to be looked up

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


async def route_targets(
    targets: Sequence[str], *, paths_only: bool = False
) -> list[Route] | None:
    """One :class:`Route` per target, or None outside workspace mode.

    ``paths_only`` reads every target without a path shape as a path anyway
    (``get_risk`` and ``get_health`` take only paths, and ``Makefile`` is one).
    """
    registry = _state._registry
    if registry is None:
        return None
    roots = _repo_roots()
    default = registry.get_default_alias()
    routes = [_route_one(t, roots, default, paths_only) for t in targets]
    names = [r.target for r in routes if r.name]
    if names and len(roots) > 1:
        owners = await _symbol_owners(names, roots, default)
        routes = [_route_name(r, owners, roots, default) if r.name else r for r in routes]
    return routes


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
    from repowise.server.mcp_server._savings.interaction import note_error_reason

    note_error_reason("not_found" if unresolved else "invalid_args")
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


def _miss_hint(target: str) -> str:
    """Name an unknown repo prefix; keep the search hint for ordinary misses."""
    registry = _state._registry
    if registry is None:
        return _MISS_HINT
    head, sep, rest = _LINE_SUFFIX_RE.sub("", target).partition(":")
    head = head.strip()
    if not head or not sep or not rest:
        return _MISS_HINT
    # Symbol references and target kinds are not repository prefixes.
    if rest.startswith(":") or head.casefold() in _TARGET_KINDS:
        return _MISS_HINT
    # A path separator or Windows drive identifies a filesystem path.
    if "/" in head or "\\" in head:
        return _MISS_HINT
    if Path(target).is_absolute() or PureWindowsPath(target).drive:
        return _MISS_HINT
    aliases = registry.get_all_aliases()
    if head.casefold() in {alias.casefold() for alias in aliases}:
        return _MISS_HINT
    return f"Unknown repository alias '{head}'. Available aliases: {', '.join(aliases)}."


def hint_misses(response: dict[str, Any], routes: list[Route]) -> None:
    """Add an alias or search hint to each missed target's not-found answer."""
    missed = {r.target: _miss_hint(r.target) for r in routes if r.missed}
    if not missed:
        return
    cards = response.get("targets")
    if isinstance(cards, dict):
        for key, card in cards.items():
            if key in missed and isinstance(card, dict) and (
                card.get("error") or card.get("unresolved_reason")
            ):
                card["hint"] = missed[key]
    for item in response.get("unresolved") or ():
        if isinstance(item, dict) and item.get("target") in missed:
            item["hint"] = missed[item["target"]]
    if response.get("error") and not isinstance(cards, dict):
        response["hint"] = " ".join(dict.fromkeys(missed.values()))


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
    The calls run through :class:`Federation`: a repo that fails or times out
    is named in ``repos_failed`` and its cards say so, while the rest answer.
    Cards carry their own freshness, so no repo-level check runs.
    """
    groups = _group([r for r in routes if r.repo is not None])
    local_targets = {
        alias: list(dict.fromkeys(r.local for r in rs)) for alias, rs in groups.items()
    }
    federation = Federation(aliases=list(groups), check_freshness=False)
    outcomes = await federation.run(lambda ctx: call(ctx.alias, local_targets[ctx.alias]))
    results: dict[str, dict[str, Any]] = {ctx.alias: result for ctx, result in outcomes}

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
        if r.repo not in results:
            cards[r.target] = {
                "repo": r.repo,
                "error": f"Repo '{r.repo}' did not answer; see repos_failed.",
            }
            continue
        result = results[r.repo]
        card = (result.get("targets") or {}).get(r.local)
        if card is None:
            card = {"error": result.get("error") or f"No result for '{r.local}'"}
        cards[r.target] = {"repo": r.repo, **card} if r.routed else card
    merged.setdefault("_meta", {})
    response = {"targets": cards, **merged}
    hint_misses(response, routes)
    return federation.annotate(response)


def other_repo_roots() -> list[tuple[str, Path]]:
    """The non-default workspace repos and their checkouts; empty outside workspace mode."""
    registry = _state._registry
    if registry is None:
        return []
    default = registry.get_default_alias()
    return [(alias, root) for alias, root in _repo_roots().items() if alias != default]


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
        return Route(target, target, default, name=bool(_IDENTIFIER_RE.fullmatch(target)))

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


def _route_name(
    route: Route, owners: dict[str, list[str]], roots: dict[str, Path], default: str
) -> Route:
    """Route a bare symbol name by the repos the locator says hold it.

    *owners* carries the default repo for a name it keeps (see
    :func:`_symbol_owners`)."""
    repos = owners.get(route.target, [])
    if not repos or default in repos or _exists(roots.get(default), route.target):
        return Route(route.target, route.local, default)
    if len(repos) == 1:
        return Route(route.target, route.local, repos[0], routed=True)
    return Route(route.target, route.local, None, candidates=tuple(repos))


_store_lock = asyncio.Lock()


async def _symbol_owners(
    names: list[str], roots: dict[str, Path], default: str
) -> dict[str, list[str]]:
    """``name -> repos holding it``, in workspace order, from the workspace store.

    Refreshes the store first when a repo's index moved. A symbol in a file its
    repo excludes does not count, and a default-repo component of that name
    counts as the default repo having it. Empty when the store is unavailable,
    so every name stays with the default repo.
    """
    from repowise.core.workspace.store import refresh_workspace_store

    registry = _state._registry
    try:
        async with _store_lock:
            path = await refresh_workspace_store(registry.ws_config, Path(registry.workspace_root))
        if path is None:
            return {}
        return await asyncio.to_thread(_owners_from_store, path, names, roots, default)
    except Exception:
        _log.warning("Workspace store lookup failed", exc_info=True)
        return {}


def _owners_from_store(
    path: Path, names: list[str], roots: dict[str, Path], default: str
) -> dict[str, list[str]]:
    from repowise.core.exclusion import build_exclude_spec, is_excluded
    from repowise.core.workspace.store import lookup_symbols

    found_rows = lookup_symbols(path, names, components_of=default)
    if found_rows is None:
        return {}
    hits, default_components = found_rows
    owners: dict[str, list[str]] = {}
    # build_exclude_spec memoises by rule-file stamps; this keeps it to one
    # build per repo per call.
    specs: dict[str, Any] = {}
    for name in names:
        found: set[str] = {default} if name.casefold() in default_components else set()
        for alias, _symbol_id, file_path in hits.get(name, ()):
            if alias in found or alias not in roots:
                continue
            if alias not in specs:
                specs[alias] = build_exclude_spec(roots[alias])
            if not is_excluded(file_path, specs[alias]):
                found.add(alias)
        if found:
            owners[name] = [a for a in roots if a in found]
    return owners


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
