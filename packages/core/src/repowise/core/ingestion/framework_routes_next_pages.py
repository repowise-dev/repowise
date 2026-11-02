"""Next.js Pages Router API routes: ``pages/api/**``.

The path comes from the file's location under ``pages/api/``, as in the App
Router, but one default-exported handler serves every verb. The verbs a handler
compares ``req.method`` against are the ones it serves; a handler that compares
none is recorded as ``*``.
"""

from __future__ import annotations

import re

from .framework_routes import NEXT_APP_ROUTER_EXTS

# `[id]`, `[...slug]`, `[[...slug]]` -> `{id}` / `{slug}`.
_DYNAMIC_SEG_RE = re.compile(r"^\[+\.{0,3}(?P<name>[^\]]+)\]+$")
# A test, mock or type declaration beside a handler is not a route a
# deployment serves.
_TEST_NAME_RE = re.compile(r"\.(?:test|spec|d)$")
_NON_ROUTE_DIRS = frozenset({"__tests__", "__mocks__", "__fixtures__"})
# What says a default export is a handler: it touches a request or response,
# names Next's types, takes `(req, res)`, or is built by a handler factory
# (`export default startServerAndCreateNextHandler(server)`).
_HANDLER_RE = re.compile(
    r"\b(?:req|res|request|response)\s*\.|\bNext(?:Api)?(?:Request|Response)\b"
    r"|^[^\S\n]*(?:export\s+default|module\.exports\s*=)\s*(?:async\s+)?"
    r"(?:[\w$.]+\s*\(|function\b[^(]*\(\s*[\w$]+[^,)]*,|\(\s*[\w$]+[^,)]*,)",
    re.MULTILINE,
)

_VERBS = "GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS"
# `req.method === 'POST'` and `req.method !== 'POST'` (an early 405) both name
# a verb the handler serves; so does a `case 'POST':` under `switch (req.method)`.
_METHOD_COMPARE_RE = re.compile(rf"""\.method\s*[!=]==?\s*['"`](?P<verb>{_VERBS})['"`]""")
_METHOD_SWITCH_RE = re.compile(r"switch\s*\(\s*[\w$.]*\.method\s*\)")
_METHOD_CASE_RE = re.compile(rf"""\bcase\s+['"`](?P<verb>{_VERBS})['"`]\s*:""")
_DEFAULT_EXPORT_RE = re.compile(r"^[^\S\n]*(?:export\s+default\b|module\.exports\s*=)", re.MULTILINE)


def next_pages_api_path(rel_path: str) -> str | None:
    """The URL a ``pages/api/**`` handler serves, else None."""
    parts = rel_path.split("/")
    name = parts[-1]
    ext = next((e for e in NEXT_APP_ROUTER_EXTS if name.endswith(e)), None)
    if ext is None:
        return None
    stem = name[: -len(ext)]
    if stem == "_middleware" or _TEST_NAME_RE.search(stem):
        return None
    # The nearest `pages/api/` pair, so a nested app's own pages win.
    at = next(
        (i for i in range(len(parts) - 3, -1, -1) if parts[i] == "pages" and parts[i + 1] == "api"),
        None,
    )
    if at is None:
        return None
    if _NON_ROUTE_DIRS.intersection(parts[at + 2 : -1]):
        return None
    segments = parts[at + 1 : -1] + ([] if stem == "index" else [stem])
    out = []
    for seg in segments:
        dyn = _DYNAMIC_SEG_RE.match(seg)
        out.append("{" + dyn.group("name") + "}" if dyn else seg)
    return "/" + "/".join(out)


def next_pages_api_verbs(content: str) -> list[tuple[str, int]] | None:
    """``(verb, offset)`` per verb the handler serves, ``[("*", offset)]`` when it
    compares none, or None when the file default-exports no handler (a helper
    module kept under ``pages/api`` touches no request or response)."""
    export = _DEFAULT_EXPORT_RE.search(content)
    if export is None or _HANDLER_RE.search(content) is None:
        return None
    found = [(m.group("verb"), m.start()) for m in _METHOD_COMPARE_RE.finditer(content)]
    if _METHOD_SWITCH_RE.search(content):
        found += [(m.group("verb"), m.start()) for m in _METHOD_CASE_RE.finditer(content)]
    if not found:
        return [("*", export.start())]
    first: dict[str, int] = {}
    for verb, at in sorted(found, key=lambda f: f[1]):
        first.setdefault(verb, at)
    return list(first.items())
