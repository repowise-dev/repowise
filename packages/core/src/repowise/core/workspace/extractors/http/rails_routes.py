"""Rails (Ruby) HTTP provider dialect — ``config/routes.rb``.

Reads routes declared via ``resources``/``resource`` (with ``only`` and
``except`` options), ``namespace``, ``scope``, ``member``, ``collection``,
direct verb helpers (``get``, ``post``, etc.), and ``match ... via:``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..langs import RUBY
from .dialect import build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_PLURAL_ACTIONS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("index", ("GET",), ""),
    ("new", ("GET",), "/new"),
    ("create", ("POST",), ""),
    ("show", ("GET",), "/:id"),
    ("edit", ("GET",), "/:id/edit"),
    ("update", ("PUT", "PATCH"), "/:id"),
    ("destroy", ("DELETE",), "/:id"),
)

_SINGULAR_ACTIONS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("new", ("GET",), "/new"),
    ("create", ("POST",), ""),
    ("show", ("GET",), ""),
    ("edit", ("GET",), "/edit"),
    ("update", ("PUT", "PATCH"), ""),
    ("destroy", ("DELETE",), ""),
)

_NAMESPACE_RE = re.compile(r"\bnamespace\s+(?::(\w+)|\"([^\"]+)\"|'([^']+)')")
_SCOPE_RE = re.compile(
    r"\bscope\s+(?:path:\s*[\"']([^\"']+)[\"']|(?::(\w+)|\"([^\"]+)\"|'([^']+)'))"
)
_RESOURCES_RE = re.compile(r"\b(resources|resource)\s+([^\n]+)")
_DIRECT_RE = re.compile(r"\b(get|post|put|patch|delete)\s+(?:[\"']([^\"']+)[\"']|:(\w+))")
_ROOT_RE = re.compile(r"\broot\b")
_MATCH_RE = re.compile(
    r"\bmatch\s+[\"']([^\"']+)[\"'][^\n]*?(?:via:\s*|:via\s*=>\s*)(?:\[([^\]]*)\]|%i\[([^\]]*)\]|:(\w+))"
)
_DO_BLOCK_RE = re.compile(r"\bdo(?:\s*\|[^|]*\|)?\s*$")


@dataclass
class _Scope:
    kind: str
    prefix: str = ""
    resource: str = ""
    singular: bool = False


def _parse_options(text: str) -> tuple[set[str] | None, set[str]]:
    only_m = re.search(
        r"only:\s*(?:\[([^\]]*)\]|%i\[([^\]]*)\]|:(\w+)|\"(\w+)\"|'(\w+)')", text
    )
    except_m = re.search(
        r"except:\s*(?:\[([^\]]*)\]|%i\[([^\]]*)\]|:(\w+)|\"(\w+)\"|'(\w+)')", text
    )
    only: set[str] | None = None
    if only_m:
        raw = next(g for g in only_m.groups() if g is not None)
        only = set(re.findall(r"\w+", raw))
    excluded: set[str] = set()
    if except_m:
        raw = next(g for g in except_m.groups() if g is not None)
        excluded = set(re.findall(r"\w+", raw))
    return only, excluded


def _current_base(stack: list[_Scope]) -> str:
    parts: list[str] = []
    for s in stack:
        if s.prefix:
            parts.append(s.prefix.strip("/"))
        elif s.resource:
            parts.append(s.resource.strip("/"))
            if not s.singular:
                parts.append(":id")
    return "/" + "/".join(parts) if parts else ""


class RailsRoutesDialect:
    name = "rails"
    extensions = RUBY
    requires = ("routes.draw",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        mask = ctx._mask()
        code = mask if mask is not None else ctx.content
        lines = code.splitlines()

        stack: list[_Scope] = []
        out: list[Contract] = []

        # Join lines that continue via trailing comma
        statements: list[tuple[int, str]] = []
        buf = ""
        start_line = 1
        for line_no, mask_line in enumerate(lines, 1):
            line = mask_line.strip()
            if not line or ctx.comment_only_line(line_no):
                continue
            if not buf:
                start_line = line_no
                buf = line
            else:
                buf += " " + line
            if not buf.endswith(","):
                statements.append((start_line, buf))
                buf = ""
        if buf:
            statements.append((start_line, buf))

        for line_no, line in statements:
            if (
                line == "end"
                or line.startswith("end ")
                or line.endswith(" end")
                or "; end" in line
            ):
                if stack:
                    stack.pop()
                continue

            has_do = bool(_DO_BLOCK_RE.search(line))

            if "routes.draw" in line:
                if has_do:
                    stack.append(_Scope(kind="drawer"))
                continue

            # namespace :admin do
            m = _NAMESPACE_RE.search(line)
            if m:
                ns = next(g for g in m.groups() if g is not None)
                if has_do:
                    stack.append(_Scope(kind="namespace", prefix="/" + ns))
                continue

            # scope "/api" do / scope path: "/api" do / scope :api do
            m = _SCOPE_RE.search(line)
            if m:
                sc = next(g for g in m.groups() if g is not None)
                prefix = ("/" + sc.strip("/")) if sc else ""
                if has_do:
                    stack.append(_Scope(kind="scope", prefix=prefix))
                continue

            # member do / collection do
            if re.search(r"\bmember\b", line) and has_do:
                stack.append(_Scope(kind="member"))
                continue
            if re.search(r"\bcollection\b", line) and has_do:
                stack.append(_Scope(kind="collection"))
                continue

            # resources / resource
            m = _RESOURCES_RE.search(line)
            if m:
                kind = m.group(1)
                rest = m.group(2)
                first_arg = rest.split(",")[0]
                names = re.findall(r":(\w+)|[\"']([^\"']+)[\"']", first_arg)
                res_names = [g[0] or g[1] for g in names]
                only, excluded = _parse_options(rest)
                is_singular = kind == "resource"
                actions = _SINGULAR_ACTIONS if is_singular else _PLURAL_ACTIONS
                for name in res_names:
                    base = _current_base(stack)
                    for act_name, verbs, suffix in actions:
                        if (only is None or act_name in only) and act_name not in excluded:
                            res_path = f"{base}/{name}{suffix}"
                            for v in verbs:
                                c = build_provider_contract(
                                    ctx,
                                    method=v,
                                    path_raw=res_path,
                                    framework="rails",
                                    line=line_no,
                                )
                                if c is not None:
                                    out.append(c)
                    if has_do:
                        stack.append(
                            _Scope(kind="resource", resource=name, singular=is_singular)
                        )
                continue

            # root
            if _ROOT_RE.search(line):
                base = _current_base(stack) or "/"
                c = build_provider_contract(
                    ctx,
                    method="GET",
                    path_raw=base,
                    framework="rails",
                    line=line_no,
                )
                if c is not None:
                    out.append(c)
                continue

            # match ... via:
            m = _MATCH_RE.search(line)
            if m:
                path = m.group(1)
                v_list = next(g for g in m.groups()[1:] if g is not None)
                if v_list == "all":
                    verbs = ["GET", "POST", "PUT", "PATCH", "DELETE"]
                else:
                    verbs = [v.upper() for v in re.findall(r"\w+", v_list)]
                for v in verbs:
                    c = build_provider_contract(
                        ctx,
                        method=v,
                        path_raw=path,
                        framework="rails",
                        line=line_no,
                    )
                    if c is not None:
                        out.append(c)
                continue

            # direct verbs: get, post, etc.
            m = _DIRECT_RE.search(line)
            if m:
                verb = m.group(1).upper()
                p = m.group(2) or m.group(3)
                if p.startswith("/"):
                    base = "".join(s.prefix for s in stack if s.prefix)
                    full = f"{base}{p}"
                else:
                    in_coll = (
                        any(s.kind == "collection" for s in stack)
                        or "on: :collection" in line
                    )
                    res = next((s for s in reversed(stack) if s.resource), None)
                    if res:
                        base = "".join(s.prefix for s in stack if s.prefix)
                        if in_coll or res.singular:
                            full = f"{base}/{res.resource}/{p}"
                        else:
                            full = f"{base}/{res.resource}/:id/{p}"
                    else:
                        base = _current_base(stack)
                        full = f"{base}/{p}"
                c = build_provider_contract(
                    ctx,
                    method=verb,
                    path_raw=full,
                    framework="rails",
                    line=line_no,
                )
                if c is not None:
                    out.append(c)
                if has_do:
                    stack.append(_Scope(kind="other"))
                continue

            if has_do:
                stack.append(_Scope(kind="other"))

        return out
