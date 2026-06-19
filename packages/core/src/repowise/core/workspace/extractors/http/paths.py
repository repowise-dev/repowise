"""HTTP path normalization and URL helpers shared by every HTTP dialect.

A contract's identity is its normalized ``method::path`` pair, so all dialects
funnel their raw matches through :func:`normalize_http_path` to compare on equal
footing regardless of the framework's parameter syntax.
"""

from __future__ import annotations

import re


def normalize_http_path(path: str) -> str:
    """Normalize an HTTP path for matching.

    Steps:
      1. Strip whitespace
      2. Remove query string
      3. Lowercase
      4. Strip trailing slash (but keep root ``/``)
      5. Unify param styles: ``:param``, ``{param}``, ``[param]`` → ``{param}``
    """
    s = path.strip().split("?")[0].lower()
    if s != "/":
        s = s.rstrip("/")
    # ASP.NET routes commonly omit the leading slash; add one so all
    # frameworks compare on equal footing.
    if s and not s.startswith("/") and not s.startswith("http"):
        s = "/" + s
    # ASP.NET route constraints: `{id:int}` / `{slug:regex(\d+)}` — strip the
    # ``:type`` portion so the next normalisation step doesn't double-wrap it.
    s = re.sub(r"(\{[a-z_][\w]*):[^}]+(\})", r"\1\2", s)
    # Unify Express :param (must run before {…} so it doesn't eat braces).
    s = re.sub(r":(\w+)", "{param}", s)
    # Unify Spring/FastAPI {name} → {param}
    s = re.sub(r"\{[^}]+\}", "{param}", s)
    # Unify Next.js [name] → {param}
    s = re.sub(r"\[[^\]]+\]", "{param}", s)
    # Unify JS template literal ${expr} → {param}
    s = re.sub(r"\$\{[^}]+\}", "{param}", s)
    return s or "/"


def extract_path_from_url(url: str) -> str:
    """Extract just the path portion from a URL, stripping scheme and host."""
    # If it starts with http:// or https://, strip scheme + authority
    if "://" in url:
        after_scheme = url.split("://", 1)[1]
        slash_idx = after_scheme.find("/")
        if slash_idx >= 0:
            return after_scheme[slash_idx:]
        return "/"
    return url


# A leading base/host placeholder in a consumer URL, e.g. ``${API_BASE}/users``
# or ``${apiUrl}/v1/users``. The value is unresolved at extraction time, so the
# only thing we can match on is the concrete suffix. Anchored at the start so
# interior expressions (real path params like ``/users/${id}``) are untouched.
_LEADING_BASE_EXPR_RE = re.compile(r"^\s*\$\{[^}]+\}")


def strip_leading_base_expr(path: str) -> tuple[str, bool]:
    """Strip a leading base/host placeholder from a consumer URL path.

    Returns ``(path, stripped)``. ``${API_BASE}/users`` becomes ``/users`` with
    ``stripped=True``. Treating the placeholder as an ordinary ``{param}`` path
    segment (which is what :func:`normalize_http_path` would otherwise do)
    prevents the route from ever lining up with a concrete provider path, so we
    drop it and let the matcher link on the suffix as a lower-confidence
    candidate instead.
    """
    new_path = _LEADING_BASE_EXPR_RE.sub("", path, count=1)
    if new_path == path:
        return path, False
    if not new_path.startswith("/"):
        new_path = "/" + new_path
    return new_path, True


def consumer_meta(method: str, norm_path: str, client: str, base_stripped: bool) -> dict:
    """Build a consumer contract's ``meta`` dict.

    ``base_stripped`` is recorded only when True so the matcher knows the path
    had an unresolved base prefix removed and should be linked as a candidate
    rather than an exact match.
    """
    meta = {"method": method, "path": norm_path, "client": client}
    if base_stripped:
        meta["base_stripped"] = True
    return meta
