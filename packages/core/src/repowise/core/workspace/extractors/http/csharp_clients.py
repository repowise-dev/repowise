"""Where a C# HttpClient call's URL is completed: client registrations and wrapper methods.

A C# call rarely names its whole URL. The client's ``BaseAddress`` is set where
it is registered (``AddHttpClient<CatalogService>(o => o.BaseAddress =
new("http://catalog-api"))``, a named ``AddHttpClient("catalog", ...)``, a Refit
``AddRefitClient<ICatalogApi>()`` or ``RestService.For<ICatalogApi>(url)``),
usually in another file. A service often calls HttpClient through methods of
its own (``_http.HttpGet<T>("catalog-items")``), also in another file. Both are
read once per file in the HTTP mounts pass and reach every file of the repo
through ``ScanContext.mounts``:

* ``csharp-client:<Type>``: the base of a typed client or Refit interface;
* ``csharp-named-client:<name>``: the base of a named client;
* ``csharp-wrapper:<Type>.<Method>``: ``<VERB> <index> <template>`` for a method
  whose body calls HttpClient with a URL built from its string parameter
  *index*; the template is that URL with the parameter as :data:`ARG`. Keyed by
  the declaring type and each interface it lists, since callers hold the
  interface.

A base counts only when it is absolute, so the host can place the call. A key
two files give different values is dropped by the mounts merge.

Ceiling: a registration is read in the repo that holds it, so a typed client
another repo registers (a shared component library) keeps its relative path and
is matched on that; a workspace-wide registration table is the upgrade.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import TYPE_CHECKING

from ..strings import CSHARP_SYNTAX, call_arguments, match_paren, resolve_string
from .mounts import declare_mount, declared_mount
from .paths import absolute_host

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .csharp_http import _FileNames

# A generic type argument, two levels of nesting (`<List<Item>>`).
GENERIC = r"<(?:[^<>]|<(?:[^<>]|<[^<>]*>)*>)*>"

# HttpClient + wrapper calls, ending at the argument list. Method verbs are
# PascalCase (C# convention); the `Async`/`Request` suffix is required so we
# don't match unrelated `Get("key")` lookups. Group 2 holds the
# `System.Net.Http.Json` and `GetStringAsync` forms.
VERB_CALL_RE = re.compile(
    r"\b(Get|Post|Put|Delete|Patch)(FromJson|AsJson|String|ByteArray|Stream)?(?:Async|Request)"
    rf"\s*(?:{GENERIC})?\s*\("
)
# Extension methods only HttpClient has; `GetStringAsync` is also a cache's.
JSON_FORMS = frozenset({"FromJson", "AsJson"})

TYPED_CLIENT_KEY = "csharp-client:"
NAMED_CLIENT_KEY = "csharp-named-client:"
WRAPPER_KEY = "csharp-wrapper:"
#: Where a wrapper template takes its caller's argument.
ARG = "\x01"

_TYPED_REGISTRATION_RE = re.compile(
    r"\bAdd(?:HttpClient|RefitClient)\s*<\s*(?:[\w.]+\s*,\s*)?(?P<type>[\w.]+)\s*>\s*\("
)
_NAMED_REGISTRATION_RE = re.compile(
    r"\bAddHttpClient\s*\(\s*(?P<name>\"[^\"\n]*\"|nameof\s*\(\s*[\w.]+\s*\)|[A-Za-z_][\w.]*\s*[,)])"
)
_REFIT_FOR_RE = re.compile(r"\bRestService\s*\.\s*For\s*<\s*(?P<type>[\w.]+)\s*>\s*\(")
# One more call in a registration's fluent chain (`.ConfigureHttpClient(...)`).
_CHAIN_LINK_RE = re.compile(r"\s*\.\s*\w+\s*(?:<[^<>()]*>)?\s*\(")
_MAX_CHAIN = 8
_BASE_ADDRESS_RE = re.compile(
    r"\bBaseAddress\s*=\s*(?:new\s*(?:(?:System\s*\.\s*)?Uri\s*)?(?P<paren>\()|(?P<name>[A-Za-z_][\w.]*))"
)
_NAMEOF_RE = re.compile(r"nameof\s*\(\s*([\w.]+)\s*\)")

# A type declaration and the base list after its `:`, up to its body.
_TYPE_DECL_RE = re.compile(
    r"(?<![\w-])(?:class|record|struct|interface)\s+"
    r"(?!(?:public|private|protected|internal|static|sealed|abstract|partial)\b)"
    r"(?P<name>[A-Za-z_]\w*)[^{;]*?(?::(?P<bases>[^{;]*))?\{"
)
_INTERFACE_NAME_RE = re.compile(r"(?<![\w.])I[A-Z]\w*")
# A method with a block body; `new X(...) {` is an object initializer.
_METHOD_DECL_RE = re.compile(
    r"(?<!new )\b(?P<name>[A-Z]\w*)\s*(?:<[^<>()]*>)?\s*\((?P<params>[^()]*)\)\s*(?:where\b[^{;]*)?\{"
)
_STRING_PARAM_RE = re.compile(r"\s*(?:\[[^\]]*\]\s*)?string\??\s+(\w+)\b")
_RECEIVER_RE = re.compile(r"(?<![\w.])(?:this\s*\.\s*)?([A-Za-z_]\w*)\s*\.\s*$")
_RECEIVER_WINDOW = 120


def client_name(token: str, names: _FileNames, offset: int) -> str | None:
    """A named client's name: ``"catalog"``, ``nameof(CatalogClient)``, or a constant holding one."""
    token = token.strip().rstrip(",)").strip()
    m = _NAMEOF_RE.fullmatch(token)
    if m is not None:
        return m.group(1).rsplit(".", 1)[-1]
    name = names.resolve(token, offset)
    return name if name and "${" not in name else None


def absolute_base(url: str | None) -> str | None:
    """*url* when it names a host, else ``None``."""
    return url if url is not None and absolute_host(url) is not None else None


_AUTHORITY_RE = re.compile(r"^[A-Za-z][\w+.-]*://[^/]*")


def with_base(base: str, url: str) -> str:
    """*url* resolved against an HttpClient ``BaseAddress``, as .NET resolves it.

    An absolute URL or one with its own leading base keeps it; ``/x`` replaces
    the base's path; ``x`` replaces the base's last segment unless the base
    ends in ``/`` (``http://h/api`` + ``x`` is ``http://h/x``).
    """
    if "://" in url or url.startswith(("//", "${")):
        return url
    authority = _AUTHORITY_RE.match(base)
    if authority is None:
        return url
    if url.startswith("/"):
        return authority.group(0) + url
    if authority.end() < len(base):
        base = base[: base.rfind("/") + 1]
    return base.rstrip("/") + "/" + url


def _in_code(names: _FileNames, offset: int) -> bool:
    """True when *offset* is code, not a comment or a string body."""
    return names.code[offset] == names.content[offset]


def _chain_end(content: str, paren: int) -> int:
    close = match_paren(content, paren)
    for _ in range(_MAX_CHAIN):
        link = _CHAIN_LINK_RE.match(content, close + 1) if close >= 0 else None
        if link is None:
            break
        nxt = match_paren(content, link.end() - 1)
        if nxt < 0:
            break
        close = nxt
    return close


def _base_address(names: _FileNames, start: int, end: int) -> str | None:
    content = names.content
    m = _BASE_ADDRESS_RE.search(content, start, end)
    if m is None:
        return None
    if m.group("paren") is not None:
        args = call_arguments(content, m.start("paren"))
        if not args or len(args) > 2:
            return None
        expr = args[0] if len(args) == 1 else f"new Uri({args[0]}, {args[1]})"
    else:
        expr = m.group("name")
    return absolute_base(names.resolve(expr, m.start()))


def _declare(out: dict[str, str], key: str, base: str | None) -> None:
    """Record a registration; one whose base is unread or disagrees still counts.

    A type registered twice, once with a base this cannot read, has no base
    the reader can trust, so the merge leaves it declared but unreadable.
    """
    entries = declare_mount(key, base)
    if key in out and out[key] != entries[key]:
        entries = declare_mount(key, None)
    out.update(entries)


def registrations(names: _FileNames) -> dict[str, str]:
    """The typed, named and Refit client bases this file registers, as mount entries."""
    content = names.content
    out: dict[str, str] = {}
    if "AddHttpClient" not in content and "AddRefitClient" not in content and "RestService" not in content:
        return out
    for m in _TYPED_REGISTRATION_RE.finditer(content):
        if _in_code(names, m.start()):
            base = _base_address(names, m.end() - 1, _chain_end(content, m.end() - 1) + 1)
            _declare(out, TYPED_CLIENT_KEY + m.group("type").rsplit(".", 1)[-1], base)
    for m in _NAMED_REGISTRATION_RE.finditer(content):
        name = client_name(m.group("name"), names, m.start())
        if name is not None and _in_code(names, m.start()):
            paren = content.index("(", m.start())
            base = _base_address(names, paren, _chain_end(content, paren) + 1)
            _declare(out, NAMED_CLIENT_KEY + name, base)
    if "RestService" in content:
        for m in _REFIT_FOR_RE.finditer(content):
            args = call_arguments(content, m.end() - 1)
            if args and _in_code(names, m.start()):
                base = absolute_base(names.resolve(args[0], m.start()))
                _declare(out, TYPED_CLIENT_KEY + m.group("type").rsplit(".", 1)[-1], base)
    return out


def client_base(mounts: Mapping[str, str], key: str) -> str | None:
    """The base registered under *key*, ``None`` when undeclared, unread or ambiguous."""
    return declared_mount(mounts, key)[1]


def declared_types(code: str) -> tuple[list[int], list[tuple[str, tuple[str, ...]]]]:
    """Each type declaration's offset, and its name with the interfaces it lists."""
    starts: list[int] = []
    types: list[tuple[str, tuple[str, ...]]] = []
    for m in _TYPE_DECL_RE.finditer(code):
        bases = re.sub(GENERIC, "", m.group("bases") or "")
        starts.append(m.start())
        types.append((m.group("name"), tuple(_INTERFACE_NAME_RE.findall(bases))))
    return starts, types


def type_at(
    declared: tuple[list[int], list[tuple[str, tuple[str, ...]]]], offset: int
) -> tuple[str, tuple[str, ...]] | None:
    """The type whose declaration is nearest above *offset*.

    Ceiling: a member after a nested type's body reads as the nested type's;
    a brace-depth walk is the upgrade if that shape turns up.
    """
    i = bisect_right(declared[0], offset) - 1
    return declared[1][i] if i >= 0 else None


def receiver_at(content: str, offset: int) -> str | None:
    """The name a member call at *offset* is made on (``client`` in ``client.GetAsync``)."""
    m = _RECEIVER_RE.search(content, max(0, offset - _RECEIVER_WINDOW), offset)
    return m.group(1) if m else None


def declared_type(code: str, name: str) -> str | None:
    """The type *code* declares *name* with (a field, parameter or local), else ``None``."""
    n = re.escape(name)
    m = re.search(rf"(?<![\w.])(?:[\w]+\.)*([A-Z]\w*)(?:{GENERIC})?\??[ \t]+{n}[ \t]*[;=,)]", code)
    return m.group(1) if m else None


def created_client(content: str, name: str) -> str | None:
    """The name token of the factory client *name* is bound to, else ``None``.

    ``var client = _factory.CreateClient("catalog")`` gives ``"catalog"``; a
    client created with no name gives an empty token.
    """
    n = re.escape(name)
    m = re.search(rf"\b{n}\s*=\s*(?:await\s+)?[\w.]*CreateClient\s*\((?P<arg>[^()]*)\)", content)
    return m.group("arg") if m else None


def is_http_client(names: _FileNames, receiver: str | None) -> bool:
    """True when the file declares *receiver* an ``HttpClient`` or binds it to a factory client."""
    if receiver is None:
        return False
    if declared_type(names.code, receiver) == "HttpClient":
        return True
    return created_client(names.content, receiver) is not None


def _params(text: str) -> list[str]:
    out: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(text):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return out


def _template(names: _FileNames, start: int, end: int, params: dict[str, int]) -> str | None:
    """The one ``<VERB> <index> <template>`` the body between *start* and *end* calls, else ``None``."""
    content = names.content
    found: set[str] = set()
    for v in VERB_CALL_RE.finditer(content, start, end):
        if v.group(2) not in JSON_FORMS and not is_http_client(names, receiver_at(content, v.start())):
            continue  # a cache's `GetStringAsync(key)` does not make its caller a URL
        args = call_arguments(content, v.end() - 1)
        if not args:
            continue
        url = names.url(args[0], v.start())
        used = [p for p in params if re.search(rf"(?<![\w.]){re.escape(p)}\b", url)]
        if len(used) != 1:
            continue
        hole = "${" + used[0] + "}"
        if url.strip() == used[0]:
            text: str | None = hole
        else:
            text = resolve_string(url, CSHARP_SYNTAX, names.fields([url]))
        if text is None or text.count(hole) != 1:
            continue
        found.add(f"{v.group(1).upper()} {params[used[0]]} {text.replace(hole, ARG)}")
    return found.pop() if len(found) == 1 else None


def wrappers(names: _FileNames) -> dict[str, str]:
    """This file's HttpClient wrapper methods, as mount entries."""
    if "HttpClient" not in names.content:
        return {}
    code = names.code
    out: dict[str, str] = {}
    for m in _METHOD_DECL_RE.finditer(code):
        params = {
            pm.group(1): i
            for i, p in enumerate(_params(m.group("params")))
            if (pm := _STRING_PARAM_RE.match(p))
        }
        if not params:
            continue
        close = match_paren(code, m.end() - 1, closer="}")
        if close < 0:
            continue
        template = _template(names, m.end(), close, params)
        owner = type_at(names.types, m.start()) if template is not None else None
        if owner is None:
            continue
        type_name, interfaces = owner
        for t in (type_name, *interfaces):
            out[f"{WRAPPER_KEY}{t}.{m.group('name')}"] = template
    return out


__all__ = [
    "ARG",
    "GENERIC",
    "JSON_FORMS",
    "NAMED_CLIENT_KEY",
    "TYPED_CLIENT_KEY",
    "VERB_CALL_RE",
    "WRAPPER_KEY",
    "absolute_base",
    "client_base",
    "client_name",
    "created_client",
    "declared_type",
    "declared_types",
    "is_http_client",
    "receiver_at",
    "registrations",
    "type_at",
    "with_base",
    "wrappers",
]
