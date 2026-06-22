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


def _after_authority(url: str) -> str | None:
    """Return the portion of *url* after scheme+authority, or ``None`` if relative.

    Handles both ``scheme://host/path`` and protocol-relative ``//host/path``.
    """
    if url.startswith("//"):
        return url[2:]
    if "://" in url:
        return url.split("://", 1)[1]
    return None


def extract_path_from_url(url: str) -> str:
    """Extract just the path portion from a URL, stripping scheme and host."""
    after = _after_authority(url)
    if after is None:
        return url
    slash_idx = after.find("/")
    return after[slash_idx:] if slash_idx >= 0 else "/"


def absolute_host(url: str) -> str | None:
    """Return the lower-cased host (sans port) of an absolute URL, else ``None``.

    ``https://formspree.io/f/x`` -> ``formspree.io``; ``http://backend:8000/api``
    -> ``backend``; protocol-relative ``//cdn.example.com/x`` -> ``cdn.example.com``.
    A relative path (``/api/users``) or a base placeholder (``${API_BASE}/users``)
    has no host and returns ``None``.
    """
    after = _after_authority(url)
    if after is None:
        return None
    authority = after.split("/", 1)[0]
    if "$" in authority or "{" in authority:
        return None  # the host is itself an unresolved expression, not a literal
    return authority.split(":", 1)[0].lower() or None


# A leading base/host placeholder in a consumer URL, e.g. ``${API_BASE}/users``
# or ``${apiUrl}/v1/users``. The captured group is the inner expression, used to
# resolve the call's target service (see :func:`base_token_identifier`). Anchored
# at the start so interior expressions (real path params like ``/users/${id}``)
# are untouched.
_LEADING_BASE_EXPR_RE = re.compile(r"^\s*\$\{([^}]+)\}")


def strip_leading_base_expr(path: str) -> tuple[str, str]:
    """Strip a leading base/host placeholder from a consumer URL path.

    Returns ``(path, base_token)``. ``${API_BASE}/users`` becomes
    ``("/users", "API_BASE")``; a path with no leading placeholder returns
    ``(path, "")``. Treating the placeholder as an ordinary ``{param}`` segment
    (which is what :func:`normalize_http_path` would otherwise do) prevents the
    route from lining up with a concrete provider path, so we drop it and let the
    matcher resolve the call's target service from the suffix and the token.
    """
    m = _LEADING_BASE_EXPR_RE.match(path)
    if m is None:
        return path, ""
    new_path = path[m.end() :]
    if not new_path.startswith("/"):
        new_path = "/" + new_path
    return new_path, m.group(1).strip()


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def base_token_identifier(base_token: str) -> str:
    """Reduce a base expression to its trailing identifier for config lookup.

    ``import.meta.env.VITE_API_URL`` -> ``VITE_API_URL``; ``process.env.API_BASE``
    -> ``API_BASE``; ``apiUrl`` -> ``apiUrl``. Empty when the token holds no
    identifier (e.g. a bare string concatenation).
    """
    ids = _IDENTIFIER_RE.findall(base_token)
    return ids[-1] if ids else ""


def is_unusable_consumer_path(path: str) -> bool:
    """True when a normalized consumer path can never be a meaningful match key.

    Two cases, both pure noise rather than a real cross-repo call:

    - an **unbalanced** ``${`` survived normalization (a template literal whose
      capture was truncated by a nested quote, e.g. ``/repos/explore${qs``);
    - the path has **no concrete segment** — every segment is ``{param}`` (or it
      is bare ``/``), so it would match indiscriminately.
    """
    if "${" in path:
        return True
    segments = [s for s in path.split("/") if s]
    return not any(s != "{param}" for s in segments)


def consumer_meta(
    method: str,
    norm_path: str,
    client: str,
    base_token: str = "",
    host: str | None = None,
) -> dict:
    """Build a consumer contract's ``meta`` dict.

    ``base_stripped`` / ``base_token`` record an unresolved base placeholder so
    the matcher resolves the call's target service from the suffix; ``host``
    records a literal absolute host so the matcher can exclude third-party APIs.
    """
    meta: dict = {"method": method, "path": norm_path, "client": client}
    if base_token:
        meta["base_stripped"] = True
        ident = base_token_identifier(base_token)
        if ident:
            meta["base_token"] = ident
    if host:
        meta["host"] = host
    return meta
