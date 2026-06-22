"""Router mount-prefix resolution shared by the HTTP provider dialects.

A framework's real route path is rarely the string in the decorator alone. It is
that string stitched onto the prefix of the *router object* it hangs off:

    router = APIRouter(prefix="/snapshots")     # in-file binding
    @router.get("/{id}/c4")                      # real path: /snapshots/{id}/c4

and, one level up, the mount the router is attached at in a different file:

    include_router(snapshots_router, prefix="/api")   # real: /api/snapshots/{id}/c4

Recovering both is the single highest-leverage recall fix for cross-repo contract
matching: without it a provider stores ``/{param}/c4`` and never lines up with the
consumer that calls ``/api/snapshots/{id}/c4``. The helpers here find the in-file
``var -> prefix`` bindings and compose the segments; cross-file mounts are
collected per dialect and merged by the orchestrator.
"""

from __future__ import annotations

import re

# A ``prefix="..."`` / ``prefix='...'`` keyword argument inside a constructor call.
_PREFIX_KW_RE = re.compile(r"""prefix\s*=\s*['"]([^'"]+)['"]""")


def balanced_args(content: str, open_paren: int) -> str:
    """Return the text inside the parentheses opened at *open_paren*.

    Scans to the matching close paren so nested calls in the argument list
    (``dependencies=[Depends(x)]``) don't truncate the span the way a
    ``[^)]*`` regex would.
    """
    depth = 0
    for i in range(open_paren, len(content)):
        ch = content[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return content[open_paren + 1 : i]
    return content[open_paren + 1 :]


def router_prefixes(content: str, ctor_pattern: str) -> dict[str, str]:
    """Map ``var -> prefix`` for in-file ``var = Ctor(...)`` router bindings.

    *ctor_pattern* is a regex alternation of constructor names (e.g.
    ``APIRouter|FastAPI``). A constructor with no ``prefix=`` keyword maps to the
    empty string, which still records the variable as a *known* router so its
    routes are extracted.
    """
    out: dict[str, str] = {}
    for m in re.finditer(rf"(\w+)\s*=\s*(?:{ctor_pattern})\s*\(", content):
        var = m.group(1)
        args = balanced_args(content, m.end() - 1)  # m.end()-1 is the '('
        pm = _PREFIX_KW_RE.search(args)
        out[var] = pm.group(1) if pm else ""
    return out


def compose_prefix(prefix: str, path: str) -> str:
    """Stitch a router *prefix* onto a method-level *path*.

    ``("/snapshots", "/{id}/c4")`` -> ``"/snapshots/{id}/c4"``. An empty prefix
    returns the path unchanged. Normalization (case, trailing slash, param
    unification) is left to :func:`normalize_http_path` downstream.
    """
    if not prefix:
        return path
    return "/" + prefix.strip("/") + "/" + path.lstrip("/")


def merge_mount_maps(maps: list[dict[str, str]]) -> dict[str, str]:
    """Merge per-file cross-file mount maps into one unambiguous ``var -> prefix``.

    A router variable mounted at two *different* prefixes anywhere in the repo is
    ambiguous (the common case being a generic name like ``router`` reused across
    files), so it is dropped rather than guessed. Only names with a single
    distinct prefix survive.
    """
    collected: dict[str, set[str]] = {}
    for m in maps:
        for var, prefix in m.items():
            collected.setdefault(var, set()).add(prefix)
    return {var: next(iter(prefixes)) for var, prefixes in collected.items() if len(prefixes) == 1}
