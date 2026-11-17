"""Server base paths applied to the routes they prefix, before matching.

A Spring service with ``server.servlet.context-path: /uaa`` declares
``@PostMapping("/users")`` and serves ``POST /uaa/users``; its callers name the
full path. The base path is often set in another repo (a config server's
``auth-service.yml``), so it is applied here, once the service-name map has
placed it, rather than at extraction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.workspace.extractors.http.mounts import compose_prefix
from repowise.core.workspace.extractors.http.paths import normalize_http_path

if TYPE_CHECKING:
    from collections.abc import Iterable

    from repowise.core.workspace.contracts import Contract
    from repowise.core.workspace.service_names import BasePath

#: Frameworks a Spring base path prefixes. Ceiling: JAX-RS under Spring Boot
#: (Jersey) and Micronaut's own ``micronaut.server.context-path`` are not read.
_FRAMEWORKS = frozenset({"spring"})
#: The path a provider declared, kept so a re-run recomputes rather than stacks.
DECLARED_PATH = "declared_path"


def _base_path_for(contract: Contract, by_repo: dict[str, list[BasePath]]) -> str:
    """The base path of the deepest module holding *contract*'s file, ``""`` when none."""
    best: BasePath | None = None
    for b in by_repo.get(contract.repo, ()):
        inside = not b.module or contract.file_path.startswith(b.module + "/")
        if inside and (best is None or len(b.module) > len(best.module)):
            best = b
    return best.path if best is not None else ""


def _declared_path(c: Contract) -> str | None:
    """The path a Spring route provider declared, None for any other contract."""
    if c.role != "provider" or c.contract_type != "http" or "method" not in c.meta:
        return None
    if c.meta.get("framework") not in _FRAMEWORKS:
        return None
    return c.meta.get(DECLARED_PATH, c.meta.get("path"))


def apply_base_paths(contracts: list[Contract], base_paths: Iterable[BasePath]) -> None:
    """Prefix each Spring provider route with its module's base path, in place.

    Idempotent: a stored contract carried into the next run keeps the path it
    declared in ``meta["declared_path"]``, and its id is rebuilt from that, so a
    base path that changed or went away is applied fresh rather than stacked.
    """
    by_repo: dict[str, list[BasePath]] = {}
    for b in base_paths:
        by_repo.setdefault(b.repo, []).append(b)
    for c in contracts:
        declared = _declared_path(c)
        if declared is None:
            continue
        base = _base_path_for(c, by_repo)
        path = normalize_http_path(compose_prefix(base, declared)) if base else declared
        c.contract_id = f"http::{c.meta['method']}::{path}"
        c.meta["path"] = path
        if base:
            c.meta[DECLARED_PATH] = declared
        else:
            c.meta.pop(DECLARED_PATH, None)
