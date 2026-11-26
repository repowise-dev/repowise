"""Contract links inside one repository, read from ``.repowise/contracts.json``.

``contracts.in_repo`` links a client call to the route it reaches when both
live in the same checkout (a server and its API client, two services of a
monorepo). The workspace readers serve links between repos; these serve the
links inside the one repo the tool answers for, through the same
:class:`CrossRepoEnricher` load and indexes.

Two readers:

* :func:`attach_contract_lines` puts one line on a get_context card: who in
  this repo calls the routes a file or handler serves, or which routes a
  client file calls. Rows sit behind ``include=["callers"]`` / ``["callees"]``.
* :func:`route_breaks` tells get_change_risk which in-repo callers lose their
  route when a change stops serving a (method, path) they call.

Only exact links reach the default line; a candidate link appears only in the
``include`` rows, labelled with its ``match_type``. A file with no link gets
nothing, not an empty field.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from repowise.server.mcp_server._enrichment import CrossRepoEnricher

_log = logging.getLogger("repowise.mcp.repo_contracts")

#: Call sites or routes named on the one default line.
_TOP = 3
#: Rows an ``include`` request lists; the total is always stated.
DETAIL_LIMIT = 20
#: Routes and callers per route named by get_change_risk's rows.
_BREAK_ROUTES_LIMIT = 5
_BREAK_CALLERS_LIMIT = 5
#: Head-side files read to tell a moved route from a removed one. Past it, a
#: route moved into an unread file reads as gone; a wider read is the upgrade.
_HEAD_FILES_LIMIT = 100

_EXACT = "exact"


@dataclass(frozen=True)
class RepoContracts:
    """The repo's own contract store and the alias its rows were written under."""

    enricher: CrossRepoEnricher
    repo: str
    #: Files serving a route an exact in-repo link calls.
    provider_files: frozenset[str] = frozenset()

    def provided(self, target: str) -> list[dict]:
        """In-repo links whose provider is *target* (a file, or ``path::Symbol``)."""
        path, _, symbol = target.partition("::")
        if symbol:
            links = self.enricher.get_contract_links_by_provider_symbol(target)
            links = [lk for lk in links if lk.get("provider_repo") == self.repo]
        else:
            links = self.enricher.get_contract_links_as_provider(self.repo, path)
        return [lk for lk in links if lk.get("consumer_repo") == self.repo]

    def consumed(self, target: str) -> list[dict]:
        """In-repo links whose consumer is *target* (a file, or ``path::Symbol``)."""
        path, _, symbol = target.partition("::")
        links = [
            lk
            for lk in self.enricher.get_contract_links_as_consumer(self.repo, path)
            if lk.get("provider_repo") == self.repo
        ]
        if symbol:
            links = [lk for lk in links if lk.get("consumer_symbol_id") == target]
        return links


_cache: dict[str, tuple[tuple[int, int], RepoContracts | None]] = {}
_cache_lock = threading.Lock()


def load_repo_contracts(repo_path: Any) -> RepoContracts | None:
    """The repo's contract links, or None without a store or without links.

    Re-read when the file changes (an update rewrites it), else served from
    memory. Never raises.
    """
    if repo_path is None:
        return None
    from repowise.core.workspace.repo_contracts import repo_contracts_path

    path = repo_contracts_path(Path(repo_path))
    try:
        stat = path.stat()
    except OSError:
        return None
    stamp = (stat.st_mtime_ns, stat.st_size)
    key = str(path)
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    loaded: RepoContracts | None = None
    try:
        enricher = CrossRepoEnricher(None, contracts_path=path)
        # One repo wrote the store, so every in-repo link names it on both ends.
        repos = {
            lk.get("provider_repo")
            for lk in enricher.contract_links
            if lk.get("provider_repo") == lk.get("consumer_repo")
        }
        if len(repos) == 1:
            repo = str(next(iter(repos)))
            providers = frozenset(
                lk.get("provider_file") or ""
                for lk in enricher.contract_links
                if lk.get("match_type") == _EXACT
                and lk.get("provider_repo") == repo
                and lk.get("consumer_repo") == repo
            )
            loaded = RepoContracts(enricher, repo, providers)
    except Exception:
        _log.debug("reading %s failed", path, exc_info=True)
    with _cache_lock:
        _cache[key] = (stamp, loaded)
    return loaded


# --------------------------------------------------------------------------
# get_context
# --------------------------------------------------------------------------


def _route(contract_id: str) -> str:
    """``http::GET::/x`` -> ``GET /x``; other types keep their id after the type."""
    kind, _, rest = contract_id.partition("::")
    if kind == "http":
        return rest.replace("::", " ", 1)
    return rest or contract_id


def _where(file: str, line: int | None) -> str:
    return f"{file}:{line}" if line else file


def _n(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _sites(store: RepoContracts, links: list[dict], side: str) -> list[dict]:
    """One row per (file, line, route) the *side* end of *links* was read at."""
    rows: dict[tuple[str, int | None, str], dict] = {}
    for lk in links:
        file = lk.get(f"{side}_file") or ""
        lines: list[int | None] = list(store.enricher.link_lines(lk, side)) or [None]
        for line in lines:
            key = (file, line, lk.get("contract_id") or "")
            if key in rows:
                continue
            row: dict[str, Any] = {"route": _route(lk.get("contract_id") or ""), "file": file}
            if line:
                row["line"] = line
            if symbol_id := lk.get(f"{side}_symbol_id"):
                row["symbol_id"] = symbol_id
            if lk.get("match_type") != _EXACT:
                row["match_type"] = lk.get("match_type")
            # The far end's origin only: the strategy is the same on every row.
            if origin := store.enricher.link_provenance(lk, side).get("origin"):
                row["origin"] = origin
            rows[key] = row
    return sorted(rows.values(), key=lambda r: (r["file"], r.get("line") or 0, r["route"]))


def _by_file(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """*rows* grouped by file, the file with the most rows first."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["file"], []).append(row)
    return sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def _exact(links: list[dict]) -> list[dict]:
    return [lk for lk in links if lk.get("match_type") == _EXACT]


def _tail(total: int, shown: int, include: str | None) -> str:
    """`` and N more`` with where the rest are listed."""
    if total <= shown:
        return ""
    return f" and {total - shown} more" + (
        f' (include=["{include}"] lists them)' if include else ""
    )


def _named_sites(sites: list[dict]) -> tuple[str, int]:
    """``file:12,20`` for up to :data:`_TOP` sites, most-calling file first."""
    parts: list[str] = []
    shown = 0
    for file, rows in _by_file(sites):
        if shown >= _TOP:
            break
        lines = [str(r["line"]) for r in rows if r.get("line")][: _TOP - shown]
        parts.append(f"{file}:{','.join(lines)}" if lines else file)
        shown += max(len(lines), 1)
    return ", ".join(parts), shown


def _caller_line(
    store: RepoContracts, links: list[dict], include: str | None
) -> tuple[str, list[dict]] | None:
    # The line counts exact links only; a candidate shows only in the rows.
    exact = _exact(links)
    if not exact:
        return None
    sites = _sites(store, exact, "consumer")
    routes = len({lk.get("contract_id") for lk in exact})
    named, shown = _named_sites(sites)
    text = (
        f"{_n(routes, 'route')} called from {_n(len(sites), 'place')} in this repo"
        + (f": {named}" if named else "")
        + _tail(len(sites), shown, include)
    )
    return text, _sites(store, links, "consumer")


def _route_line(
    store: RepoContracts, links: list[dict], include: str | None
) -> tuple[str, list[dict]] | None:
    # The line counts exact links only; a candidate shows only in the rows.
    exact = _exact(links)
    if not exact:
        return None
    by_route: dict[str, dict] = {}
    for row in _sites(store, exact, "provider"):
        by_route.setdefault(row["route"], row)
    parts: list[str] = []
    shown = 0
    for file, rows in _by_file(list(by_route.values())):
        if shown >= _TOP:
            break
        named = [
            f"{r['route']} L{r['line']}" if r.get("line") else r["route"]
            for r in rows[: _TOP - shown]
        ]
        parts.append(f"{file} ({', '.join(named)})")
        shown += len(named)
    text = (
        f"calls {_n(len(by_route), 'route')} served in this repo"
        + (f": {'; '.join(parts)}" if parts else "")
        + _tail(len(by_route), shown, include)
    )
    return text, _sites(store, links, "provider")


def attach_contract_lines(response: dict[str, Any], repo_path: Any, include: set[str]) -> None:
    """Put the in-repo contract line on each get_context card it applies to.

    Default: one string, ``in_repo_contracts``. ``include=["callers"]`` adds
    ``in_repo_callers`` (who calls the routes a target serves) and
    ``include=["callees"]`` adds ``in_repo_routes`` (the routes it calls), each
    capped at :data:`DETAIL_LIMIT` with its total. A card that already carries
    workspace contract rows is left alone. Never raises.
    """
    try:
        store = load_repo_contracts(repo_path)
        targets = response.get("targets")
        if store is None or not isinstance(targets, dict):
            return
        for target, card in targets.items():
            if not isinstance(card, dict) or "error" in card:
                continue
            if (card.get("cross_repo") or {}).get("contracts"):
                continue
            _attach_one(store, target, card, include)
    except Exception:
        _log.debug("in-repo contract lines failed", exc_info=True)


def _attach_one(store: RepoContracts, target: str, card: dict, include: set[str]) -> None:
    parts: list[str] = []
    callers = _caller_line(
        store, store.provided(target), None if "callers" in include else "callers"
    )
    if callers is not None:
        parts.append(callers[0])
        if "callers" in include:
            card["in_repo_callers"] = callers[1][:DETAIL_LIMIT]
            card["in_repo_callers_total"] = len(callers[1])
    routes = _route_line(store, store.consumed(target), None if "callees" in include else "callees")
    if routes is not None:
        parts.append(routes[0])
        if "callees" in include:
            card["in_repo_routes"] = routes[1][:DETAIL_LIMIT]
            card["in_repo_routes_total"] = len(routes[1])
    if parts:
        card["in_repo_contracts"] = "; ".join(parts)


# --------------------------------------------------------------------------
# get_change_risk
# --------------------------------------------------------------------------


def _segments(contract_id: str) -> tuple[str, tuple[str, ...]] | None:
    """``(METHOD, path segments)`` of a normalized HTTP id, else None."""
    from repowise.core.workspace.contracts import normalize_contract_id

    parts = normalize_contract_id(contract_id).split("::", 2)
    if len(parts) != 3 or parts[0] != "http":
        return None
    return parts[1], tuple(s for s in parts[2].split("/") if s)


def _http_providers(repo_path: Path, repo: str, sources: dict[str, bytes]) -> list[Any]:
    """HTTP provider contracts the dialects read from *sources*, each file alone.

    Alone, so a file reads the same on both sides of a change: read together,
    a mount another file declares under the same router name could add or drop
    a prefix on one side only and fake a changed path.
    """
    from repowise.core.workspace.extractors.http import HttpExtractor

    extractor = HttpExtractor()
    found: list[Any] = []
    for path, raw in sources.items():
        text = raw.decode("utf-8", errors="replace")
        found += extractor.extract(
            repo_path, repo, files=[(path, PurePosixPath(path).suffix.lower(), text)]
        )
    return [c for c in found if c.role == "provider" and c.contract_type == "http"]


def _gone_routes(base: list[Any], head: list[Any]) -> list[Any]:
    """Base-side routes no head-side file serves any more (by method and path)."""
    served = {_segments(c.contract_id) for c in head}
    return [c for c in base if (key := _segments(c.contract_id)) and key not in served]


def _stored_for(store: RepoContracts, gone: Any, stored: list[dict]) -> dict | None:
    """The stored provider link row a base-side route is, or None when unsure.

    A route read from one file lacks the prefix a mount in another file adds,
    so it matches a stored route by method and trailing path segments (a root
    path only a root path). The same line settles a tie; a tie it cannot
    settle is no claim.
    """
    key = _segments(gone.contract_id)
    if key is None:
        return None
    method, tail = key
    by_id: dict[str, dict] = {}
    for lk in stored:
        stored_key = _segments(lk.get("contract_id") or "")
        if stored_key is None or stored_key[0] != method:
            continue
        segs = stored_key[1]
        if (segs[len(segs) - len(tail) :] == tail) if tail else not segs:
            by_id.setdefault(lk.get("contract_id") or "", lk)
    if len(by_id) > 1 and gone.line:
        by_id = {
            cid: lk
            for cid, lk in by_id.items()
            if gone.line in store.enricher.link_lines(lk, "provider")
        }
    return next(iter(by_id.values())) if len(by_id) == 1 else None


@dataclass
class _Break:
    contract_id: str
    provider_file: str
    callers: list[dict]
    edited: int


def may_break_routes(repo_path: Any, changed_files: Any) -> bool:
    """Whether a change can stop serving a linked route: cheap, no git.

    True when the change edits a file serving a linked route, or such a file is
    missing from the checkout (deleted). A commit that deleted one which the
    checkout has since restored reads False; reading git for every call is the
    upgrade.
    """
    store = load_repo_contracts(repo_path)
    if store is None or not store.provider_files:
        return False
    root = Path(repo_path)
    return any(path in changed_files for path in store.provider_files) or any(
        not (root / path).exists() for path in store.provider_files
    )


def route_breaks(
    repo_path: Any, revspec: str | None, changed_files: Any = None
) -> list[_Break] | None:
    """Routes this change stops serving, each with the in-repo callers that call it.

    *revspec* None reads the working tree against HEAD. The base and head
    sides of each changed provider file with exact in-repo callers are read
    with the route dialects and compared by method and path; a route no
    changed file serves on the head side is gone (removed, or its path or
    method changed), and a route moved to another changed file is still
    served. Callers whose file the change also edits are counted apart, not
    named as broken. With *changed_files*, git is read only when
    :func:`may_break_routes` says it can matter. None without a store or
    anything to compare; never raises.
    """
    try:
        store = load_repo_contracts(repo_path)
        if store is None:
            return None
        if changed_files is not None and not may_break_routes(repo_path, changed_files):
            return None
        return _compare(Path(repo_path), store, revspec)
    except Exception:
        _log.debug("in-repo route comparison failed", exc_info=True)
        return None


def _compare(root: Path, store: RepoContracts, revspec: str | None) -> list[_Break] | None:
    from repowise.core.analysis.change_health.sources import GitRevisionSource
    from repowise.core.workspace.extractors.http import HttpExtractor

    source = GitRevisionSource(str(root), hunks=False)
    pair = source.resolve(revspec)
    # The stored rows name the files as indexed, so a changed file is looked up
    # by its base-side path; a deleted file has only that one.
    linked = {
        c.base_path: links
        for c in pair.changes
        if c.base_path
        and (links := [lk for lk in store.provided(c.base_path) if lk.get("match_type") == _EXACT])
    }
    if not linked:
        return None
    extensions = HttpExtractor.source_extensions()
    # The linked files' own head sides first, so the cap never drops one.
    changes = sorted(pair.changes, key=lambda c: c.base_path not in linked)
    head_paths = [
        c.head_path
        for c in changes
        if c.head_path and PurePosixPath(c.head_path).suffix.lower() in extensions
    ][:_HEAD_FILES_LIMIT]
    head_sources = (
        source.read_working_tree(head_paths)
        if pair.working_tree
        else source.read(pair.head_sha, head_paths)
    )
    gone = _gone_routes(
        _http_providers(root, store.repo, source.read(pair.base_sha, list(linked))),
        _http_providers(root, store.repo, head_sources),
    )
    edited = {p for c in pair.changes for p in (c.base_path, c.head_path) if p}
    breaks: dict[str, _Break] = {}
    for route in gone:
        links = linked.get(route.file_path, [])
        hit = _stored_for(store, route, links)
        if hit is None or hit.get("contract_id") in breaks:
            continue
        same = [lk for lk in links if lk.get("contract_id") == hit.get("contract_id")]
        sites = _sites(store, same, "consumer")
        callers = [r for r in sites if r["file"] not in edited]
        if callers:
            breaks[hit["contract_id"]] = _Break(
                hit["contract_id"], route.file_path, callers, len(sites) - len(callers)
            )
    return list(breaks.values())


def route_breaks_payload(breaks: list[_Break] | None, *, detail: bool) -> dict[str, Any] | None:
    """``in_repo_contracts`` (one line) and, with *detail*, ``in_repo_breaks`` rows."""
    if not breaks:
        return None
    callers = sum(len(b.callers) for b in breaks)
    first = breaks[0]
    named, shown = _named_sites(first.callers)
    line = (
        f"stops serving {_n(len(breaks), 'route')} that {_n(callers, 'place')} in this repo "
        f"call: {_route(first.contract_id)} ({first.provider_file}) from {named}"
        + _tail(callers, shown, None if detail else "findings")
    )
    out: dict[str, Any] = {"in_repo_contracts": line}
    if detail:
        out["in_repo_breaks"] = [
            {
                "contract_id": b.contract_id,
                "provider_file": b.provider_file,
                "callers": b.callers[:_BREAK_CALLERS_LIMIT],
                "callers_total": len(b.callers),
                **({"callers_edited_in_change": b.edited} if b.edited else {}),
            }
            for b in breaks[:_BREAK_ROUTES_LIMIT]
        ]
        if len(breaks) > _BREAK_ROUTES_LIMIT:
            out["in_repo_breaks_truncated"] = len(breaks) - _BREAK_ROUTES_LIMIT
    return out


__all__ = [
    "DETAIL_LIMIT",
    "RepoContracts",
    "attach_contract_lines",
    "load_repo_contracts",
    "may_break_routes",
    "route_breaks",
    "route_breaks_payload",
]
