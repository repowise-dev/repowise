"""URL builder functions imported across JS/TS modules.

A shared endpoints module often exports arrow functions or methods that return
template strings for API paths:

    export const user = () => `/api/user`;
    export const conversation = (id: string) => `/api/conv/${id}`;
    export const branching = (mode: string) => mode === 'admin' ? '/admin' : '/user';

When called from another module (``axios.get(endpoints.user())`` or
``axios.get(conversation(123))``), the call expressions resolve to their
respective route templates, with arguments substituted as ``{param}`` holes.
Builders with conditional branching or computed bodies stay unresolved and are
noted as such.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import JS_EXTENSION_RE

if TYPE_CHECKING:
    from ..base import ScanContext
    from ..calls import FileStrings

_BUILDER_KEY = "url-builder:"
_BUILDER_ANY = "url-builders-present"

_ARROW_EXPORT_RE = re.compile(
    r"""export(?<![\w$]export)\s+(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"""
    r"""(?:\((?P<params>[^)]*)\)|(?P<single>[A-Za-z_$][\w$]*))(?:\s*:[^=]+)?\s*=>\s*(?P<body>[^\n;]+)"""
)

_FN_EXPORT_RE = re.compile(
    r"""export(?<![\w$]export)\s+(?:async\s+)?function(?:\s*\*?)?\s+(?P<name>[A-Za-z_$][\w$]*)\s*"""
    r"""\((?P<params>[^)]*)\)[^{]*\{(?P<body>[^}]+)\}"""
)

_OBJ_MEMBER_RE = re.compile(
    r"""(?P<name>[A-Za-z_$][\w$]*)\s*:\s*(?:\((?P<params>[^)]*)\)|(?P<single>[A-Za-z_$][\w$]*))"""
    r"""(?:\s*:[^=]+)?\s*=>\s*(?P<body>[^\n,;]+)"""
)

_BRANCH_RE = re.compile(r"\?|\bif\b|\bswitch\b")
_LITERAL_RE = re.compile(r"""[`'"]([^`'"]*)[`'"]""")
_HOLE_RE = re.compile(r"\$\{[^{}]+\}")


def _module_name(path: str) -> str:
    parts = JS_EXTENSION_RE.sub("", path).rstrip("/").split("/")
    if parts[-1] == "index" and len(parts) > 1:
        return parts[-2]
    return parts[-1]


def normalize_builder_body(body: str) -> str | None:
    """The route template returned by *body*, '!branching', or None if unreadable."""
    body = body.strip()
    if body.startswith("return "):
        body = body[7:].strip()
    if _BRANCH_RE.search(body):
        return "!branching"
    m = _LITERAL_RE.search(body)
    if m is None:
        return "!branching" if ("+" in body or "?" in body) else None
    url = m.group(1)
    url = _HOLE_RE.sub("{param}", url)
    if "+" in body:
        url = url.rstrip("/") + "/{param}"
    if not url.startswith(("/", "http://", "https://", "${")):
        url = "/" + url
    return url


def url_builder_mounts(ctx: ScanContext) -> dict[str, str]:
    """Mount entries for exported URL builder functions defined in *ctx*."""
    content = ctx.content
    if "export" not in content and "=>" not in content:
        return {}
    module = _module_name(ctx.rel_path)
    out: dict[str, str] = {}

    for m in _ARROW_EXPORT_RE.finditer(content):
        name = m.group("name")
        template = normalize_builder_body(m.group("body"))
        if template is not None:
            out[f"{_BUILDER_KEY}{module}:{name}"] = template

    for m in _FN_EXPORT_RE.finditer(content):
        name = m.group("name")
        template = normalize_builder_body(m.group("body"))
        if template is not None:
            out[f"{_BUILDER_KEY}{module}:{name}"] = template

    if "export default" in content or "module.exports" in content:
        for m in _OBJ_MEMBER_RE.finditer(content):
            name = m.group("name")
            template = normalize_builder_body(m.group("body"))
            if template is not None and f"{_BUILDER_KEY}{module}:{name}" not in out:
                out[f"{_BUILDER_KEY}{module}:{name}"] = template

    if out:
        out[_BUILDER_ANY] = ""
    return out


def settle_url_builders(
    ctx: ScanContext, imports: list[tuple[str, str, str]], strings: FileStrings
) -> None:
    """Populate *strings.settled* with concrete URLs from imported builder calls."""
    if _BUILDER_ANY not in ctx.mounts:
        return
    builders: dict[str, str] = {}
    for local, imported, spec in imports:
        if not spec.startswith("."):
            continue
        module = _module_name(spec)
        prefix = f"{_BUILDER_KEY}{module}:"
        if imported in ("*", "default"):
            for key, template in ctx.mounts.items():
                if key.startswith(prefix):
                    name = key[len(prefix) :]
                    builders[f"{local}.{name}"] = template
        else:
            template = ctx.mounts.get(f"{prefix}{imported}")
            if template is not None:
                builders[local] = template

    if not builders:
        return

    alt = "|".join(re.escape(b) for b in sorted(builders, key=len, reverse=True))
    call_pattern = re.compile(rf"\b(?P<callee>{alt})\s*\((?P<args>[^()]*)\)")
    for m in call_pattern.finditer(ctx.content):
        callee = m.group("callee")
        full_call = m.group(0)
        template = builders[callee]
        if template == "!branching":
            ctx.note_unresolved(m.start())
        else:
            strings.settled[full_call] = template


__all__ = ["normalize_builder_body", "settle_url_builders", "url_builder_mounts"]
